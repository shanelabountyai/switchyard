"""The generated runs reach every path the invariant tests rely on, so those tests are not vacuous."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

import pytest
from hypothesis import HealthCheck, given, settings

from switchyard.engine import Engine
from switchyard.models import CacheStatus, Status
from tests.invariants.runs import Spec, run, specs


def _events(e: Engine) -> set[str]:
    return {ev.label() for t in e.traces for ev in t.events}


CONDITIONS: dict[str, Callable[[Engine], bool]] = {
    "breaker_opened": lambda e: "breaker_opened" in _events(e),
    "probe": lambda e: "probe" in _events(e),
    "breaker_closed": lambda e: "breaker_closed" in _events(e),
    "breaker_open_skip": lambda e: "breaker_open_skip" in _events(e),
    "retry": lambda e: "retry" in _events(e),
    "retry_budget_exhausted": lambda e: "no_retry:retry_budget_exhausted" in _events(e),
    "per_request_retry_limit": lambda e: "no_retry:per_request_retry_limit" in _events(e),
    "max_attempts": lambda e: "no_retry:max_attempts" in _events(e),
    "downgrade_chain": lambda e: "downgrade_chain" in {ev.code for t in e.traces for ev in t.events},
    "downgraded": lambda e: any("downgraded" in t.flags for t in e.traces),
    "fallback_used": lambda e: any("fallback_used" in t.flags for t in e.traces),
    "over_hard_limit": lambda e: any("over_hard_limit" in t.flags for t in e.traces),
    "soft_limit_crossed": lambda e: any("soft_limit_crossed" in t.flags for t in e.traces),
    "denied_budget": lambda e: any(t.status is Status.DENIED_BUDGET for t in e.traces),
    "failed_exhausted": lambda e: any(t.status is Status.FAILED_EXHAUSTED for t in e.traces),
    "bad_request_attempt": lambda e: any(
        a.outcome.value == "bad_request" for t in e.traces for a in t.attempts
    ),
    "malformed": lambda e: any(t.status is Status.BAD_REQUEST and not t.attempts for t in e.traces),
    "cache_hit": lambda e: any(t.cache is CacheStatus.HIT for t in e.traces),
    "tagged_attempt": lambda e: any(t.data_tags and t.attempts for t in e.traces),
}


MIN_SHARE_PCT = {"breaker_opened": 8, "retry": 20, "fallback_used": 15}  # outage generator, SW-06
BREAKER_PATHS = {
    "breaker_opened",
    "probe",
    "breaker_closed",
    "breaker_open_skip",
    "bad_request_attempt",
    "max_attempts",
}
MIXED_ONLY = {"cache_hit", "downgraded"}


@pytest.mark.parametrize("outage", [False, True], ids=["mixed", "outage"])
def test_generator_reaches_every_path(outage: bool) -> None:
    counts: Counter[str] = Counter()
    total = [0]

    @settings(
        max_examples=300,
        deadline=None,
        database=None,
        derandomize=True,
        suppress_health_check=list(HealthCheck),
    )
    @given(specs(max_requests=16, outage=outage))
    def sample(spec: Spec) -> None:
        e = run(spec)
        total[0] += 1
        counts.update(name for name, condition in CONDITIONS.items() if condition(e))

    sample()
    assert total[0] == 300
    required = set(CONDITIONS) - (MIXED_ONLY if outage else BREAKER_PATHS)
    missing = sorted(name for name in required if counts[name] == 0)
    assert missing == [], f"never reached: {missing} ({dict(counts)})"
    if outage:
        for name, pct in MIN_SHARE_PCT.items():
            assert counts[name] * 100 >= pct * total[0], (name, counts[name])
