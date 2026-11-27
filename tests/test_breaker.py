from __future__ import annotations

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from switchyard.breaker import (
    BREAKER_CLOSED,
    BREAKER_OPENED,
    PROBE,
    BreakerError,
    CircuitBreaker,
    RetryBudget,
)
from switchyard.models import BreakerSettings, BreakerState, Outcome, RetryBudgetSettings

SETTINGS = BreakerSettings(window_ms=30_000, min_calls=4, failure_rate_pct=50, open_ms=10_000)
RETRY = RetryBudgetSettings(window_ms=10_000, ratio_pct=20, min_retries=1)
FAIL = Outcome.SERVER_ERROR


def _call(b: CircuitBreaker, start: int, end: int, outcome: Outcome) -> str | None:
    allow = b.allow(start)
    assert allow.allowed
    return b.record(outcome, end, is_probe=allow.is_probe)


def test_alder_timeline_we06_to_we11() -> None:
    b = CircuitBreaker(SETTINGS)
    assert _call(b, 0, 50, FAIL) is None
    assert _call(b, 250, 300, FAIL) is None
    assert _call(b, 1000, 1050, FAIL) is None
    assert b.state is BreakerState.CLOSED  # 3 of 3 failed, min_calls is 4
    assert _call(b, 2000, 2050, FAIL) == BREAKER_OPENED
    assert (b.state, b.opened_at) == (BreakerState.OPEN, 2050)
    assert not b.allow(3000).allowed  # WE-09 skip
    probe = b.allow(13_000)
    assert (probe.allowed, probe.is_probe, probe.change) == (True, True, PROBE)
    assert b.record(FAIL, 13_050, is_probe=True) == BREAKER_OPENED
    assert b.opened_at == 13_050
    probe = b.allow(24_000)
    assert probe.is_probe and probe.change == PROBE
    assert b.record(Outcome.OK, 24_400, is_probe=True) == BREAKER_CLOSED
    assert b.state is BreakerState.CLOSED


def _opened(at: int = 0) -> CircuitBreaker:
    b = CircuitBreaker(SETTINGS)
    for i in range(4):
        _call(b, at + i, at + i, FAIL)
    assert b.state is BreakerState.OPEN
    return b


def test_open_ms_boundary() -> None:
    b = _opened()
    assert not b.allow(3 + 10_000 - 1).allowed
    assert b.allow(3 + 10_000).allowed


def test_outcome_exactly_window_old_is_dropped() -> None:
    b = CircuitBreaker(SETTINGS)
    _call(b, 0, 0, FAIL)
    _call(b, 1, 1, FAIL)
    _call(b, 2, 2, FAIL)
    # at 30_000 the outcome at 0 is exactly window_ms old and is dropped: 3 outcomes, below min_calls
    assert _call(b, 30_000, 30_000, FAIL) is None
    assert b.state is BreakerState.CLOSED


@pytest.mark.parametrize(
    ("outcomes", "opens"), [("FFF", False), ("OOFF", True), ("OOOF", False), ("FFFF", True)]
)
def test_failure_rate_threshold(outcomes: str, opens: bool) -> None:
    b = CircuitBreaker(SETTINGS)
    for i, o in enumerate(outcomes):
        _call(b, i, i, FAIL if o == "F" else Outcome.OK)
    assert (b.state is BreakerState.OPEN) is opens


def test_only_a_failure_can_open() -> None:
    b = CircuitBreaker(SETTINGS)
    for i, o in enumerate("FFFO"):
        _call(b, i, i, FAIL if o == "F" else Outcome.OK)
    assert b.state is BreakerState.CLOSED  # 3 of 4 failed but the last outcome was ok


def test_half_open_allows_one_probe_and_give_back() -> None:
    b = _opened()
    first = b.allow(20_000)
    assert first.is_probe
    assert not b.allow(20_001).allowed  # probe in flight
    b.cancel(is_probe=True)  # e.g. the probe's budget reserve was refused
    assert b.state is BreakerState.HALF_OPEN
    again = b.allow(20_002)
    assert again.allowed and again.is_probe and again.change is None


def test_failed_probe_not_added_to_window_and_success_clears_it() -> None:
    b = _opened()
    b.allow(20_000)
    b.record(FAIL, 20_000, is_probe=True)
    b.allow(30_000)
    b.record(Outcome.OK, 30_000, is_probe=True)
    # window was cleared on close: three new failures cannot open it
    for i in range(3):
        _call(b, 30_001 + i, 30_001 + i, FAIL)
    assert b.state is BreakerState.CLOSED


def test_record_without_allow_raises() -> None:
    b = CircuitBreaker(SETTINGS)
    with pytest.raises(BreakerError):
        b.record(Outcome.OK, 0, is_probe=False)
    with pytest.raises(BreakerError):
        b.record(Outcome.OK, 0, is_probe=True)
    b.allow(0)
    with pytest.raises(BreakerError):
        b.record(Outcome.BAD_REQUEST, 0, is_probe=False)


def test_record_while_open_changes_nothing_else() -> None:
    b = CircuitBreaker(SETTINGS)
    late = b.allow(0)  # an overlapping call, still in flight
    for i in range(4):
        _call(b, i, i, FAIL)
    assert b.state is BreakerState.OPEN
    assert b.record(Outcome.OK, 5, is_probe=late.is_probe) is None
    assert (b.state, b.opened_at) == (BreakerState.OPEN, 3)


def test_retry_budget_we06_we07() -> None:
    rb = RetryBudget(RETRY)
    rb.note_first_attempt(0)
    assert rb.counts(50) == (1, 0, 1) and rb.can_retry(50)
    rb.note_retry(250)
    rb.note_first_attempt(1000)
    assert rb.counts(1050) == (2, 1, 1) and not rb.can_retry(1050)


@pytest.mark.parametrize(("firsts", "allowed"), [(4, 1), (5, 1), (10, 2), (14, 2), (15, 3)])
def test_retry_budget_floor(firsts: int, allowed: int) -> None:
    rb = RetryBudget(RETRY)
    for i in range(firsts):
        rb.note_first_attempt(i)
    assert rb.counts(100)[2] == allowed


def test_retry_budget_window_is_strict() -> None:
    rb = RetryBudget(RETRY)
    rb.note_retry(0)
    assert not rb.can_retry(9_999)
    assert rb.can_retry(10_000)  # ts 0 is not > 10_000 - 10_000


class BreakerMachine(RuleBasedStateMachine):
    """AC-B2 and AC-B3 over random allow/record/advance sequences."""

    def __init__(self) -> None:
        super().__init__()
        self.b = CircuitBreaker(SETTINGS)
        self.now = 0
        self.in_flight: list[bool] = []  # is_probe per allowed call

    @rule(ms=st.integers(min_value=0, max_value=15_000))
    def advance(self, ms: int) -> None:
        self.now += ms

    @rule()
    def allow(self) -> None:
        state_before, opened_at = self.b.state, self.b.opened_at
        result = self.b.allow(self.now)
        if state_before is BreakerState.OPEN and self.now - opened_at < SETTINGS.open_ms:
            assert not result.allowed  # AC-B2
        if result.allowed:
            self.in_flight.append(result.is_probe)

    @precondition(lambda self: bool(self.in_flight))
    @rule(index=st.integers(min_value=0), failed=st.booleans(), cancel=st.booleans())
    def finish(self, index: int, failed: bool, cancel: bool) -> None:
        is_probe = self.in_flight.pop(index % len(self.in_flight))
        if cancel:
            self.b.cancel(is_probe=is_probe)
        else:
            self.b.record(FAIL if failed else Outcome.OK, self.now, is_probe=is_probe)

    @invariant()
    def at_most_one_probe(self) -> None:
        assert sum(1 for p in self.in_flight if p) <= 1  # AC-B3
        assert self.b.probe_in_flight == any(self.in_flight)


TestBreakerMachine = BreakerMachine.TestCase
