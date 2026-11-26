"""SW-03: append-only, gapless seq, every reserve closed exactly once, settle <= reserve."""

from __future__ import annotations

from hypothesis import given, settings

from switchyard.ledger import Ledger
from switchyard.models import EntryKind, LedgerEntry
from tests.helpers import CASES, case_result
from tests.invariants.runs import Spec, run, specs


def _check(entries: tuple[LedgerEntry, ...]) -> None:
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    reserves = {e.seq: e for e in entries if e.kind is EntryKind.RESERVE}
    closes = [e for e in entries if e.kind in (EntryKind.SETTLE, EntryKind.RELEASE)]
    assert sorted(e.ref_seq for e in closes if e.ref_seq is not None) == sorted(reserves)
    for close in closes:
        assert close.ref_seq is not None
        reserve = reserves[close.ref_seq]
        assert close.amount_micro <= reserve.amount_micro
        assert (close.request_id, close.attempt, close.period) == (
            reserve.request_id,
            reserve.attempt,
            reserve.period,
        )
        assert close.seq > reserve.seq


def test_golden_ledgers() -> None:
    for path in CASES:
        result = case_result(path)
        if "c07" in path.name:
            continue  # the interleaved case's ledger is checked by its own expectations
        _check(result.ledger.entries())


@settings(max_examples=60, deadline=None)
@given(specs())
def test_generated_ledgers(spec: Spec) -> None:
    _check(run(spec).ledger.entries())


def test_entries_are_a_copy_and_frozen() -> None:
    ledger = Ledger()
    ledger.adjust("t", 1, 0, "m")
    snapshot = ledger.entries()
    assert isinstance(snapshot, tuple)
    ledger.adjust("t", 1, 0, "m")
    assert len(snapshot) == 1 and len(ledger.entries()) == 2
