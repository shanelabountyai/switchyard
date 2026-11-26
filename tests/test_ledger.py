from __future__ import annotations

import pytest

from switchyard.clock import parse_utc
from switchyard.ledger import Ledger, LedgerError
from switchyard.models import UNSETTLED, EntryKind, LedgerEntry
from tests.helpers import START_MS, team

BUDGET = team(soft=8000, hard=10_000).budget


def _ledger(opening: int) -> Ledger:
    ledger = Ledger()
    ledger.adjust("support", opening, START_MS, "illustrative opening balance")
    return ledger


def test_we14_soft_limit_flags_without_blocking() -> None:
    ledger = _ledger(7900)
    result = ledger.reserve("support", "r1", 1, 120, START_MS, BUDGET)
    assert result.accepted and result.flags == ("soft_limit_crossed",)
    ledger.settle("r1", 1, 90, START_MS + 400)
    assert ledger.balance("support", "2026-11").spent == 7990


def test_we15_hard_limit_refuses_and_writes_nothing() -> None:
    ledger = _ledger(9950)
    result = ledger.reserve("support", "r1", 1, 120, START_MS, BUDGET)
    assert not result.accepted and result.entry is None
    assert (result.before.spent, result.ceiling) == (9950, 10_000)
    assert len(ledger.entries()) == 1


def test_we16_exactly_at_the_limit_is_accepted() -> None:
    ledger = _ledger(9880)
    assert ledger.reserve("support", "r1", 1, 120, START_MS, BUDGET).accepted


def test_we17_hold_blocks_second_request() -> None:
    ledger = _ledger(9790)
    assert ledger.reserve("support", "A", 1, 120, START_MS, BUDGET).accepted
    assert not ledger.reserve("support", "B", 1, 120, START_MS + 1, BUDGET).accepted
    ledger.settle("A", 1, 90, START_MS + 400)
    c = ledger.reserve("support", "C", 1, 120, START_MS + 401, BUDGET)
    assert c.accepted and c.flags == ("soft_limit_crossed",)
    ledger.settle("C", 1, 90, START_MS + 801)
    balance = ledger.balance("support", "2026-11")
    assert (balance.spent, balance.held) == (9970, 0)


def test_we22_rollover_starts_a_new_period() -> None:
    ledger = Ledger()
    nov = parse_utc("2026-11-30T23:59:59.000Z")
    ledger.adjust("support", 9950, nov, "opening")
    assert not ledger.reserve("support", "r1", 1, 120, nov, BUDGET).accepted
    dec = parse_utc("2026-12-01T00:00:00.000Z")
    result = ledger.reserve("support", "r2", 1, 120, dec, BUDGET)
    assert result.accepted and result.flags == () and result.entry is not None
    assert result.entry.period == "2026-12"


def test_we23_settle_after_midnight_posts_to_reserve_month() -> None:
    """AC-C6."""
    ledger = Ledger()
    before = parse_utc("2026-11-30T23:59:59.900Z")
    ledger.adjust("support", 9000, before, "opening")
    ledger.reserve("support", "r1", 1, 120, before, BUDGET)
    settle = ledger.settle("r1", 1, 90, parse_utc("2026-12-01T00:00:00.300Z"))
    assert settle.period == "2026-11"
    assert ledger.balance("support", "2026-11").spent == 9090
    assert ledger.balance("support", "2026-12").spent == 0


def _assert_raises_and_appends_nothing(ledger: Ledger, call: object) -> None:
    before = ledger.entries()
    with pytest.raises(LedgerError):
        call()  # type: ignore[operator]
    assert ledger.entries() == before


def test_ledger_errors_append_nothing() -> None:
    """AC-C4."""
    ledger = _ledger(0)
    ledger.reserve("support", "r1", 1, 120, START_MS, BUDGET)
    _assert_raises_and_appends_nothing(
        ledger, lambda: ledger.settle("r1", 1, 121, START_MS)
    )  # settle > reserve
    _assert_raises_and_appends_nothing(ledger, lambda: ledger.settle("nope", 1, 0, START_MS))  # no reserve
    _assert_raises_and_appends_nothing(
        ledger, lambda: ledger.reserve("support", "r1", 1, 10, START_MS, BUDGET)
    )  # duplicate (request_id, attempt)
    ledger.settle("r1", 1, 90, START_MS)
    _assert_raises_and_appends_nothing(ledger, lambda: ledger.settle("r1", 1, 0, START_MS))  # second settle
    _assert_raises_and_appends_nothing(ledger, lambda: ledger.release("r1", 1, START_MS, "x"))
    _assert_raises_and_appends_nothing(ledger, lambda: ledger.adjust("support", -10_000, START_MS, "x"))
    _assert_raises_and_appends_nothing(ledger, lambda: ledger.adjust("support", 5, START_MS, ""))
    _assert_raises_and_appends_nothing(
        ledger, lambda: ledger.reserve("support", "r9", 1, -1, START_MS, BUDGET)
    )


def test_money_types_rejected() -> None:
    ledger = Ledger()
    for bad in (1.0, True, "1"):
        with pytest.raises(TypeError):
            ledger.reserve("support", "r1", 1, bad, START_MS, BUDGET)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ledger.adjust("support", bad, START_MS, "x")  # type: ignore[arg-type]
    for field in ("amount_micro", "ts_ms", "seq"):
        data = {
            "seq": 1,
            "ts_ms": 0,
            "team": "t",
            "period": "2026-11",
            "kind": "adjustment",
            "amount_micro": 1,
            "memo": "m",
        }
        for bad in (1.0, True, "1"):
            with pytest.raises(ValueError):
                LedgerEntry.model_validate({**data, field: bad})


def test_seq_gapless_and_no_mutation_api() -> None:
    """AC-C5."""
    ledger = _ledger(5)
    for i in range(5):
        ledger.reserve("support", f"r{i}", 1, 10, START_MS + i, BUDGET)
        ledger.settle(f"r{i}", 1, 3, START_MS + i)
    assert [e.seq for e in ledger.entries()] == list(range(1, 12))
    public = {name for name in dir(Ledger) if not name.startswith("_")}
    assert public == {
        "adjust",
        "balance",
        "entries",
        "from_entries",
        "from_jsonl",
        "open_reserves",
        "release",
        "reserve",
        "settle",
        "sweep",
        "to_jsonl",
    }
    entry = ledger.entries()[0]
    field = "amount_micro"
    with pytest.raises(ValueError):
        setattr(entry, field, 0)  # frozen


def test_jsonl_round_trip_gives_same_balances() -> None:
    ledger = _ledger(100)
    ledger.reserve("support", "r1", 1, 120, START_MS, BUDGET)
    ledger.settle("r1", 1, 90, START_MS + 1)
    ledger.reserve("support", "r2", 1, 50, START_MS + 2, BUDGET)
    again = Ledger.from_jsonl(ledger.to_jsonl())
    assert again.entries() == ledger.entries()
    assert again.balance("support", "2026-11") == ledger.balance("support", "2026-11")
    assert again.to_jsonl() == ledger.to_jsonl()


def test_from_jsonl_rejects_a_gap() -> None:
    ledger = _ledger(100)
    ledger.reserve("support", "r1", 1, 120, START_MS, BUDGET)
    lines = ledger.to_jsonl().splitlines(keepends=True)
    with pytest.raises(LedgerError):
        Ledger.from_jsonl(lines[1])


def test_sweep_releases_stale_holds_flagged_unsettled() -> None:
    ledger = Ledger()
    ledger.reserve("support", "old", 1, 120, START_MS, BUDGET)
    ledger.reserve("support", "new", 1, 120, START_MS + 500_000, BUDGET)
    released = ledger.sweep(START_MS + 600_000, 600_000)
    assert [e.request_id for e in released] == ["old"]
    assert released[0].kind is EntryKind.RELEASE and released[0].flags == (UNSETTLED,)
    assert released[0].amount_micro == 0 and released[0].memo
    assert ledger.balance("support", "2026-11").held == 120
    assert ledger.sweep(START_MS + 600_000, 600_000) == []


def test_adjustment_posts_to_its_own_period_and_counts_toward_committed() -> None:
    ledger = Ledger()
    ledger.adjust("support", 9_950, START_MS, "opening")
    assert not ledger.reserve("support", "r1", 1, 51, START_MS, BUDGET).accepted
    assert ledger.reserve("support", "r1", 1, 50, START_MS, BUDGET).accepted
