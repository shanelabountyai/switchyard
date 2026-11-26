from __future__ import annotations

import pytest

from switchyard.clock import parse_utc
from switchyard.models import EntryKind
from switchyard.report import daily_rows, render_json, render_markdown
from switchyard.trace import ReconcileError
from tests.helpers import FIXTURES, START_MS, case_result, engine, request, team, window

CASES_DIR = FIXTURES / "cases"


def test_we30_outage_row() -> None:
    result = case_result(CASES_DIR / "c02-provider-outage.yaml")
    assert result.report_rows is not None
    (row,) = result.report_rows
    got = row.model_dump()
    want = {
        "date": "2026-11-02",
        "team": "support",
        "requests": 6,
        "ok": 6,
        "cache_hits": 0,
        "attempts": 11,
        "failed_attempts": 5,
        "input_tokens": 2400,
        "output_tokens": 72,
        "cost_micro": 650,
        "cost_usd": "0.000650",
        "spent_micro": 650,
        "fallback_used": 5,
    }
    assert {k: got[k] for k in want} == want
    entries = result.ledger.entries()
    reserves = [e.amount_micro for e in entries if e.kind is EntryKind.RESERVE]
    settles = [e.amount_micro for e in entries if e.kind is EntryKind.SETTLE]
    assert (len(entries), sum(reserves), sum(settles)) == (22, 1470, 650)
    assert sorted(reserves) == [120] * 6 + [150] * 5


def test_we23_cost_counted_on_reserve_day() -> None:
    result = case_result(CASES_DIR / "c12-period-straddle.yaml")
    assert result.report_rows is not None
    assert [(r.date, r.cost_micro, r.spent_micro) for r in result.report_rows] == [("2026-11-30", 90, 9090)]


def test_request_before_midnight_attempt_after_midnight() -> None:
    """Arrives 23:59:59.900, fails, retries after midnight: a request on day 1, a cost on day 2."""
    start = parse_utc("2026-11-30T23:59:59.900Z")
    e = engine(
        start_ms=start,
        scripts={"alder": [window(0, 150, "server_error", latency_ms=50, billed_input_tokens=400)]},
    )
    trace = e.handle(request(), fake_output_tokens=12)
    assert [a.started_ms - start for a in trace.attempts] == [0, 250]
    rows = daily_rows(e.traces, e.ledger.entries(), e.teams)
    by_day = {r.date: r for r in rows}
    assert (
        by_day["2026-11-30"].requests,
        by_day["2026-11-30"].attempts,
        by_day["2026-11-30"].cost_micro,
    ) == (1, 1, 80)
    assert (
        by_day["2026-12-01"].requests,
        by_day["2026-12-01"].attempts,
        by_day["2026-12-01"].cost_micro,
    ) == (0, 1, 90)
    assert by_day["2026-12-01"].period == "2026-12" and by_day["2026-12-01"].spent_micro == 90


def test_daily_costs_plus_adjustments_equal_spent() -> None:
    """AC-E4."""
    for path in sorted(CASES_DIR.glob("*.yaml")):
        result = case_result(path)
        if result.report_rows is None:
            continue
        for team_name in {r.team for r in result.report_rows}:
            for period in {r.period for r in result.report_rows}:
                rows = [r for r in result.report_rows if r.team == team_name and r.period == period]
                total = sum(r.cost_micro + r.adjustments_micro for r in rows)
                assert total == result.ledger.balance(team_name, period).spent, (path.stem, team_name, period)


def test_unknown_team_row_has_blank_limits() -> None:
    e = engine(teams={"support": team()})
    e.handle(request("r1", team_name="ghost"))
    (row,) = daily_rows(e.traces, e.ledger.entries(), e.teams)
    assert (row.team, row.unknown_team, row.hard_limit_micro, row.remaining_micro) == ("ghost", 1, None, None)


def test_tampered_trace_makes_the_report_refuse() -> None:
    result = case_result(CASES_DIR / "c02-provider-outage.yaml")
    first = result.traces[0]
    a = first.attempts[-1].model_copy(update={"cost_micro": 111})
    tampered = first.model_copy(update={"attempts": (*first.attempts[:-1], a), "cost_micro": 111})
    with pytest.raises(ReconcileError):
        daily_rows([tampered, *result.traces[1:]], result.ledger.entries(), result.loaded.teams)


def test_render_labels_illustrative_and_is_stable() -> None:
    result = case_result(CASES_DIR / "c02-provider-outage.yaml")
    assert result.report_rows is not None
    text = render_markdown(result.report_rows)
    assert "Illustrative prices, fake providers. Not deployed." in "\n".join(text.splitlines()[:4])
    assert "estimate, not in the ledger" in text
    assert render_json(result.report_rows) == render_json(result.report_rows)
    assert START_MS > 0
