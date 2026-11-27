"""Reconciliation (PRD 8.2) and AC-E3 mutation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from switchyard.models import EntryKind, LedgerEntry, Trace
from switchyard.trace import from_jsonl, reconcile, to_jsonl
from tests.helpers import CASES, FIXTURES, case_result

OUTAGE = FIXTURES / "cases" / "c02-provider-outage.yaml"


def _base() -> tuple[list[Trace], list[LedgerEntry]]:
    result = case_result(OUTAGE)
    return list(result.traces), list(result.ledger.entries())


@pytest.mark.parametrize("path", [p for p in CASES if "c07" not in p.name], ids=lambda p: p.stem)
def test_golden_cases_reconcile_clean(path: Path) -> None:
    result = case_result(path)
    assert reconcile(result.traces, result.ledger.entries()) == []


def test_changing_any_one_attempt_cost_is_detected() -> None:
    traces, entries = _base()
    for t_index, trace in enumerate(traces):
        for a_index, attempt in enumerate(trace.attempts):
            changed_attempt = attempt.model_copy(update={"cost_micro": attempt.cost_micro + 1})
            attempts = list(trace.attempts)
            attempts[a_index] = changed_attempt
            changed = trace.model_copy(
                update={"attempts": tuple(attempts), "cost_micro": trace.cost_micro + 1}
            )
            mutated = list(traces)
            mutated[t_index] = changed
            assert reconcile(mutated, entries), (trace.request_id, attempt.attempt)


def test_changing_a_trace_total_alone_is_detected() -> None:
    traces, entries = _base()
    tampered = traces[0].model_construct(**{**traces[0].__dict__, "cost_micro": traces[0].cost_micro + 1})
    assert reconcile([tampered, *traces[1:]], entries)


def test_removing_any_one_reserve_or_settle_line_is_detected() -> None:
    traces, entries = _base()
    for index, entry in enumerate(entries):
        assert entry.kind in (EntryKind.RESERVE, EntryKind.SETTLE)
        assert reconcile(traces, entries[:index] + entries[index + 1 :]), entry.seq


def test_swapping_two_zero_cost_settle_seqs_is_detected() -> None:
    traces, entries = _base()
    first = traces[0]  # WE-06: two failed attempts that settled 0
    a1, a2 = first.attempts[0], first.attempts[1]
    assert a1.cost_micro == a2.cost_micro == 0
    swapped = first.model_copy(
        update={
            "attempts": (
                a1.model_copy(update={"settle_seq": a2.settle_seq}),
                a2.model_copy(update={"settle_seq": a1.settle_seq}),
                *first.attempts[2:],
            )
        }
    )
    assert reconcile([swapped, *traces[1:]], entries)


def test_ledger_integrity_checks() -> None:
    traces, entries = _base()
    settle = next(e for e in entries if e.kind is EntryKind.SETTLE)
    over = settle.model_copy(update={"amount_micro": 10_000})
    assert any(
        d.code == "settle_exceeds_reserve"
        for d in reconcile(traces, [over if e is settle else e for e in entries])
    )
    dup = [*entries, settle.model_copy(update={"seq": len(entries) + 1})]
    assert any(d.code == "reserve_not_closed_once" for d in reconcile(traces, dup))
    wrong_ref = settle.model_copy(update={"ref_seq": 3})
    assert reconcile(traces, [wrong_ref if e is settle else e for e in entries])


def test_jsonl_round_trip_and_no_message_text() -> None:
    """AC-E5: the trace holds the cache key hash, never the message text."""
    for path in CASES:
        result = case_result(path)
        text = to_jsonl(result.traces)
        assert from_jsonl(text) == result.traces
        assert "Illustrative" not in text  # every fixture message starts with "Illustrative"
        assert '"content"' not in text and '"messages"' not in text
