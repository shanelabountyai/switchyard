"""SW-06 / AC-B2 to AC-B6: breaker and retry-budget bounds hold under generated outage scripts."""

from __future__ import annotations

from collections import defaultdict

from hypothesis import given, settings

from switchyard.models import Attempt, Event, Outcome
from tests.helpers import policy
from tests.invariants.runs import Spec, run, specs

ENGINE = policy().engine


def _in_window(times: list[int], now: int) -> int:
    return sum(1 for t in times if t > now - ENGINE.retry_budget.window_ms)


@settings(max_examples=100, deadline=None)
@given(specs(max_requests=16, outage=True))
def test_bounds(spec: Spec) -> None:
    e = run(spec)
    opened_at: dict[str, int] = {}
    firsts: dict[str, list[int]] = defaultdict(list)
    retries: dict[str, list[int]] = defaultdict(list)
    for trace in e.traces:
        assert len(trace.attempts) <= ENGINE.max_attempts  # AC-B4
        timeline: list[tuple[int, int, Event | None, Attempt | None]] = sorted(
            [(a.started_ms, 1, None, a) for a in trace.attempts]
            + [(ev.at_ms, 0, ev, None) for ev in trace.events],
            key=lambda item: (item[0], item[1]),
        )
        seen: set[str] = set()
        per_provider_retries: dict[str, int] = defaultdict(int)
        previous: Attempt | None = None
        for _, _, event, attempt in timeline:
            if event is not None:
                if event.code == "breaker_opened":
                    opened_at[event.subject] = event.at_ms
                elif event.code == "breaker_closed":
                    opened_at.pop(event.subject, None)
                elif event.code == "retry":
                    provider = e.catalog.model(event.subject).provider
                    allowed = max(
                        ENGINE.retry_budget.min_retries,
                        _in_window(firsts[provider], event.at_ms) * ENGINE.retry_budget.ratio_pct // 100,
                    )
                    assert _in_window(retries[provider], event.at_ms) < allowed  # AC-B5
                continue
            assert attempt is not None
            provider = attempt.provider
            if provider in opened_at:
                assert attempt.started_ms - opened_at[provider] >= ENGINE.breaker.open_ms  # AC-B2
            is_retry = (
                previous is not None and previous.model == attempt.model and previous.outcome.is_failure
            )
            if provider not in seen:
                seen.add(provider)
                firsts[provider].append(attempt.started_ms)
            elif is_retry:
                retries[provider].append(attempt.started_ms)
                per_provider_retries[provider] += 1
            previous = attempt
        assert all(n <= ENGINE.max_retries_per_provider for n in per_provider_retries.values())  # AC-B4
        for i, attempt_ in enumerate(trace.attempts):  # AC-B6: bad_request ends the request
            if attempt_.outcome is Outcome.BAD_REQUEST:
                assert i == len(trace.attempts) - 1
