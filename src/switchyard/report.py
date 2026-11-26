"""Daily per-team cost report (PRD 8.3). Runs ``reconcile`` first and refuses to print totals on a difference.

Which day each column belongs to:

- arrival day of the request: ``requests``, status counts, ``cache_hits``, flag counts, ``avoided_micro``;
- reserve day of the attempt (accounting time): ``attempts``, ``failed_attempts``, tokens and cost;
- the day of its own ``ts_ms``: ``adjustments_micro``;
- period-to-date columns: the period containing the row's day, as of the end of that day by accounting time.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from switchyard.canonical import canonical_json
from switchyard.clock import day_of
from switchyard.models import (
    DOWNGRADED,
    FALLBACK_USED,
    OVER_HARD_LIMIT,
    SOFT_LIMIT_CROSSED,
    CacheStatus,
    EntryKind,
    LedgerEntry,
    ReportRow,
    Status,
    TeamConfig,
    Trace,
)
from switchyard.money import format_usd
from switchyard.trace import ReconcileError, reconcile

_COUNTERS = (
    "requests",
    *(s.value for s in Status),
    "cache_hits",
    "attempts",
    "failed_attempts",
    "input_tokens",
    "output_tokens",
    "cost_micro",
    "avoided_micro",
    "adjustments_micro",
    SOFT_LIMIT_CROSSED,
    OVER_HARD_LIMIT,
    DOWNGRADED,
    FALLBACK_USED,
)


def daily_rows(
    traces: Sequence[Trace], entries: Sequence[LedgerEntry], teams: Mapping[str, TeamConfig]
) -> list[ReportRow]:
    differences = reconcile(traces, entries)
    if differences:
        raise ReconcileError(differences)
    by_seq = {e.seq: e for e in entries}
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: dict.fromkeys(_COUNTERS, 0))
    for trace in traces:
        row = counts[(trace.team, day_of(trace.arrived_ms))]
        row["requests"] += 1
        row[trace.status.value] += 1
        if trace.cache is CacheStatus.HIT:
            row["cache_hits"] += 1
            row["avoided_micro"] += trace.avoided_micro
        for flag in trace.flags:
            row[flag] += 1
        for attempt in trace.attempts:
            reserve_day = day_of(by_seq[attempt.reserve_seq].ts_ms)
            cost_row = counts[(trace.team, reserve_day)]
            cost_row["attempts"] += 1
            cost_row["failed_attempts"] += 1 if attempt.outcome.is_failure else 0
            cost_row["input_tokens"] += attempt.input_tokens
            cost_row["output_tokens"] += attempt.output_tokens
            cost_row["cost_micro"] += attempt.cost_micro
    for entry in entries:
        if entry.kind is EntryKind.ADJUSTMENT:
            counts[(entry.team, day_of(entry.ts_ms))]["adjustments_micro"] += entry.amount_micro
        elif entry.kind is EntryKind.RESERVE:
            counts[(entry.team, day_of(entry.ts_ms))]  # a reserve alone makes a row exist

    rows: list[ReportRow] = []
    for team, day in sorted(counts, key=lambda k: (k[1], k[0])):
        values = counts[(team, day)]
        period = day[:7]
        spent = _period_to_date(entries, by_seq, team, period, day)
        config = teams.get(team)
        soft = config.budget.soft_limit_micro if config else None
        hard = config.budget.hard_limit_micro if config else None
        rows.append(
            ReportRow(
                date=day,
                team=team,
                cost_usd=format_usd(values["cost_micro"]),
                period=period,
                spent_micro=spent,
                soft_limit_micro=soft,
                hard_limit_micro=hard,
                remaining_micro=None if hard is None else hard - spent,
                **values,
            )
        )
    return rows


def _period_to_date(
    entries: Sequence[LedgerEntry], by_seq: Mapping[int, LedgerEntry], team: str, period: str, day: str
) -> int:
    """``spent`` for the period at the end of ``day``: settles by reserve day, adjustments by own day."""
    total = 0
    for entry in entries:
        if entry.team != team or entry.period != period:
            continue
        if entry.kind is EntryKind.ADJUSTMENT and day_of(entry.ts_ms) <= day:
            total += entry.amount_micro
        elif entry.kind is EntryKind.SETTLE:
            assert entry.ref_seq is not None
            if day_of(by_seq[entry.ref_seq].ts_ms) <= day:
                total += entry.amount_micro
    return total


_MD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("date", "date"),
    ("team", "team"),
    ("requests", "requests"),
    ("ok", "ok"),
    ("no_route", "no_route"),
    ("denied_budget", "denied_budget"),
    ("failed_exhausted", "failed_exhausted"),
    ("bad_request", "bad_request"),
    ("unknown_team", "unknown_team"),
    ("cache_hits", "cache_hits"),
    ("attempts", "attempts"),
    ("failed_attempts", "failed_attempts"),
    ("input_tokens", "input_tokens"),
    ("output_tokens", "output_tokens"),
    ("cost_micro", "cost_micro"),
    ("cost_usd", "cost_usd"),
    ("avoided_micro", "avoided_micro (estimate, not in the ledger)"),
    ("adjustments_micro", "adjustments_micro"),
    ("soft_limit_crossed", "requests with reserves above the soft limit"),
    ("over_hard_limit", "requests with reserves above the hard limit"),
    ("downgraded", "downgraded"),
    ("fallback_used", "fallback_used"),
    ("spent_micro", "period-to-date spent_micro"),
    ("soft_limit_micro", "soft_limit_micro"),
    ("hard_limit_micro", "hard_limit_micro"),
    ("remaining_micro", "remaining_micro"),
)


def render_markdown(rows: Sequence[ReportRow], title: str = "Daily cost report") -> str:
    lines = [
        f"# {title}",
        "",
        "Illustrative prices, fake providers. Not deployed. Every number below was computed by",
        "Switchyard from a golden case run under a fake clock. Money is integer micro-dollars (µUSD)",
        "unless the column says USD.",
        "",
        "| " + " | ".join(label for _, label in _MD_COLUMNS) + " |",
        "|"
        + "|".join("---" if name in ("date", "team", "cost_usd") else "---:" for name, _ in _MD_COLUMNS)
        + "|",
    ]
    for row in rows:
        data = row.model_dump()
        cells = ["" if data[name] is None else str(data[name]) for name, _ in _MD_COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "Notes:",
        "",
        "- Requests, status counts, cache hits, flag counts and `avoided_micro` are counted on the UTC",
        "  day the request arrived. Attempts, tokens and cost are counted on the UTC day of each attempt's",
        "  reserve, so a request that arrives before midnight and makes an attempt after it is a request",
        "  on the first day and a cost on the second.",
        "- `failed_attempts` counts timeout, server_error and rate_limited attempts (PRD 5.1), not",
        "  bad_request.",
        "- Flag columns count requests that carried the flag. The soft and hard limit flags are set on",
        "  reserves, so they describe holds at worst-case cost, not final spend.",
        "- `avoided_micro` is an estimate of what cache hits would have cost. It is not in the ledger.",
        "- Period-to-date `spent_micro` is for the UTC calendar month of the row, as of the end of the",
        "  row's day.",
        "  `remaining_micro` is `hard_limit_micro - spent_micro`. A team with no config shows blank limits.",
        "- This report ran `reconcile` first and found no difference between the traces and the ledger.",
        "",
    ]
    return "\n".join(lines)


def render_json(rows: Sequence[ReportRow]) -> str:
    return canonical_json([row.model_dump(mode="json") for row in rows]) + "\n"
