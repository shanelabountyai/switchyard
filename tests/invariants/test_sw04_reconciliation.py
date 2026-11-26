"""SW-04 / AC-E2 / AC-E4: traces add up exactly to the ledger, and the report to both, for generated runs."""

from __future__ import annotations

from hypothesis import given, settings

from switchyard.models import EntryKind
from switchyard.report import daily_rows
from switchyard.trace import reconcile
from tests.invariants.runs import Spec, run, specs


@settings(max_examples=80, deadline=None)
@given(specs())
def test_traces_sum_to_the_ledger(spec: Spec) -> None:
    e = run(spec)
    entries = e.ledger.entries()
    assert reconcile(e.traces, entries) == []
    assert len(e.traces) == len(spec.requests)  # AC-E1: one trace per request
    # every attempt cost equals its settle; per-team totals equal the ledger
    settles = {x.seq: x.amount_micro for x in entries if x.kind is EntryKind.SETTLE}
    assert sum(t.cost_micro for t in e.traces) == sum(settles.values())
    for t in e.traces:
        assert t.cost_micro == sum(settles[a.settle_seq] for a in t.attempts)
    assert e.ledger.open_reserves() == ()
    rows = daily_rows(e.traces, entries, e.teams)
    for team_name in {r.team for r in rows}:
        for period in {r.period for r in rows}:
            mine = [r for r in rows if r.team == team_name and r.period == period]
            total = sum(r.cost_micro + r.adjustments_micro for r in mine)
            assert total == e.ledger.balance(team_name, period).spent
