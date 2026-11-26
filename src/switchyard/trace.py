"""Trace JSON Lines and reconciliation of traces against the ledger (PRD 8.1, 8.2).

``reconcile`` returns a list of differences. An empty list is the only passing result.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from switchyard.canonical import canonical_json
from switchyard.clock import day_of
from switchyard.models import EntryKind, LedgerEntry, Trace


@dataclass(frozen=True, order=True)
class Difference:
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class ReconcileError(ValueError):
    def __init__(self, differences: Sequence[Difference]) -> None:
        self.differences = list(differences)
        super().__init__("; ".join(str(d) for d in differences))


def to_jsonl(traces: Iterable[Trace]) -> str:
    return "".join(canonical_json(t.model_dump(mode="json")) + "\n" for t in traces)


def from_jsonl(text: str) -> list[Trace]:
    return [Trace.model_validate_json(line) for line in text.splitlines() if line]


def _ledger_integrity(entries: Sequence[LedgerEntry]) -> list[Difference]:
    diffs: list[Difference] = []
    by_seq: dict[int, LedgerEntry] = {}
    for position, entry in enumerate(entries, start=1):
        if entry.seq != position:
            diffs.append(Difference("seq_gap", f"position {position} holds seq {entry.seq}"))
        by_seq[entry.seq] = entry
    closes: dict[int, list[LedgerEntry]] = defaultdict(list)
    for entry in entries:
        if entry.kind not in (EntryKind.SETTLE, EntryKind.RELEASE):
            continue
        assert entry.ref_seq is not None
        reserve = by_seq.get(entry.ref_seq)
        if reserve is None or reserve.kind is not EntryKind.RESERVE:
            diffs.append(
                Difference("bad_ref_seq", f"seq {entry.seq} points at {entry.ref_seq}, not a reserve")
            )
            continue
        if (reserve.request_id, reserve.attempt) != (entry.request_id, entry.attempt):
            diffs.append(
                Difference("ref_mismatch", f"seq {entry.seq} closes seq {reserve.seq} of another attempt")
            )
        if (reserve.team, reserve.period) != (entry.team, entry.period):
            diffs.append(
                Difference("period_mismatch", f"seq {entry.seq} is not posted to its reserve's period")
            )
        if entry.amount_micro > reserve.amount_micro:
            diffs.append(
                Difference("settle_exceeds_reserve", f"seq {entry.seq} settles more than seq {reserve.seq}")
            )
        closes[reserve.seq].append(entry)
    for entry in entries:
        if entry.kind is EntryKind.RESERVE and len(closes[entry.seq]) != 1:
            diffs.append(
                Difference(
                    "reserve_not_closed_once", f"seq {entry.seq} closed {len(closes[entry.seq])} times"
                )
            )
    return diffs


def _compare[K](label: str, ours: dict[K, int], theirs: dict[K, int]) -> list[Difference]:
    return [
        Difference(label, f"{key}: traces {ours.get(key, 0)}, ledger {theirs.get(key, 0)}")
        for key in sorted(set(ours) | set(theirs), key=str)
        if ours.get(key, 0) != theirs.get(key, 0)
    ]


def reconcile(traces: Sequence[Trace], entries: Sequence[LedgerEntry]) -> list[Difference]:
    """Every check in PRD 8.2. Assumes nothing is in flight."""
    diffs = _ledger_integrity(entries)
    by_seq = {e.seq: e for e in entries}
    claimed: set[int] = set()
    trace_cost_by_request: dict[str, int] = defaultdict(int)
    trace_cost_by_team_period: dict[tuple[str, str], int] = defaultdict(int)
    trace_cost_by_team_day: dict[tuple[str, str], int] = defaultdict(int)
    for trace in traces:
        if trace.cost_micro != sum(a.cost_micro for a in trace.attempts):
            diffs.append(
                Difference("trace_cost", f"request {trace.request_id}: cost is not the sum of attempts")
            )
        trace_cost_by_request[trace.request_id] += trace.cost_micro
        for attempt in trace.attempts:
            where = f"request {trace.request_id} attempt {attempt.attempt}"
            reserve = by_seq.get(attempt.reserve_seq)
            settle = by_seq.get(attempt.settle_seq)
            claimed.update((attempt.reserve_seq, attempt.settle_seq))
            if reserve is None or reserve.kind is not EntryKind.RESERVE:
                diffs.append(Difference("missing_reserve", f"{where}: reserve_seq {attempt.reserve_seq}"))
            else:
                if reserve.amount_micro != attempt.reserved_micro:
                    diffs.append(
                        Difference(
                            "reserved_mismatch",
                            f"{where}: trace {attempt.reserved_micro}, ledger {reserve.amount_micro}",
                        )
                    )
                if (reserve.request_id, reserve.attempt, reserve.team) != (
                    trace.request_id,
                    attempt.attempt,
                    trace.team,
                ):
                    diffs.append(Difference("reserve_owner", f"{where}: seq {reserve.seq} belongs elsewhere"))
                trace_cost_by_team_period[(trace.team, reserve.period)] += attempt.cost_micro
                trace_cost_by_team_day[(trace.team, day_of(reserve.ts_ms))] += attempt.cost_micro
            if settle is None or settle.kind is not EntryKind.SETTLE:
                diffs.append(Difference("missing_settle", f"{where}: settle_seq {attempt.settle_seq}"))
                continue
            if settle.amount_micro != attempt.cost_micro:
                diffs.append(
                    Difference(
                        "cost_mismatch", f"{where}: trace {attempt.cost_micro}, ledger {settle.amount_micro}"
                    )
                )
            if settle.ref_seq != attempt.reserve_seq:
                diffs.append(
                    Difference("settle_ref", f"{where}: settle closes {settle.ref_seq}, not reserve_seq")
                )
    for entry in entries:
        if entry.kind in (EntryKind.RESERVE, EntryKind.SETTLE) and entry.seq not in claimed:
            diffs.append(Difference("unclaimed_entry", f"seq {entry.seq} ({entry.kind}) is in no trace"))
    # Per request, per team and period, per team and day (by accounting time: the reserve's time).
    settle_by_request: dict[str, int] = defaultdict(int)
    settle_by_team_period: dict[tuple[str, str], int] = defaultdict(int)
    settle_by_team_day: dict[tuple[str, str], int] = defaultdict(int)
    for entry in entries:
        if entry.kind is not EntryKind.SETTLE:
            continue
        assert entry.request_id is not None and entry.ref_seq is not None
        settle_by_request[entry.request_id] += entry.amount_micro
        settle_by_team_period[(entry.team, entry.period)] += entry.amount_micro
        reserve = by_seq.get(entry.ref_seq)
        if reserve is not None:
            settle_by_team_day[(entry.team, day_of(reserve.ts_ms))] += entry.amount_micro
    diffs += _compare("request_total", trace_cost_by_request, settle_by_request)
    diffs += _compare("team_period_total", trace_cost_by_team_period, settle_by_team_period)
    diffs += _compare("team_day_total", trace_cost_by_team_day, settle_by_team_day)
    return sorted(set(diffs))
