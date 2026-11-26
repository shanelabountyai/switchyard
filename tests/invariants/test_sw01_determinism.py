"""SW-01 / AC-B1: same inputs and fake clock give byte-identical traces, ledger and report."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings

from switchyard.cases import load_case, run_case
from switchyard.report import daily_rows, render_markdown
from switchyard.trace import to_jsonl
from tests.helpers import CASES
from tests.invariants.runs import Spec, run, specs


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_golden_case_twice_is_byte_identical(path: Path) -> None:
    first, second = run_case(load_case(path)), run_case(load_case(path))
    assert to_jsonl(first.traces) == to_jsonl(second.traces)
    assert first.ledger.to_jsonl() == second.ledger.to_jsonl()
    assert first.breakers == second.breakers
    if first.report_rows is not None:
        assert second.report_rows is not None
        assert render_markdown(first.report_rows) == render_markdown(second.report_rows)


@settings(max_examples=60, deadline=None)
@given(specs())
def test_generated_runs_are_deterministic(spec: Spec) -> None:
    """Routing, fallback and budgets are deterministic for the same inputs and clock."""
    a, b = run(spec), run(spec)
    assert to_jsonl(a.traces) == to_jsonl(b.traces)
    assert a.ledger.to_jsonl() == b.ledger.to_jsonl()
    assert a.health.states() == b.health.states()
    assert render_markdown(daily_rows(a.traces, a.ledger.entries(), a.teams)) == render_markdown(
        daily_rows(b.traces, b.ledger.entries(), b.teams)
    )
