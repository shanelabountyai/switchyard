"""Circuit breaker and retry budget, one of each per provider (PRD 5.2, 5.3).

State changes only inside :meth:`CircuitBreaker.allow` and :meth:`CircuitBreaker.record`. Both return the
change, if any, so the engine can write ``probe``, ``breaker_opened`` and ``breaker_closed`` events.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from switchyard.models import BreakerSettings, BreakerState, Outcome, RetryBudgetSettings

PROBE = "probe"
BREAKER_OPENED = "breaker_opened"
BREAKER_CLOSED = "breaker_closed"


class BreakerError(RuntimeError):
    """``record`` or ``cancel`` without a matching ``allow``."""


@dataclass(frozen=True)
class Allow:
    allowed: bool
    is_probe: bool
    state: BreakerState
    change: str | None


class CircuitBreaker:
    def __init__(self, settings: BreakerSettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._opened_at = 0
        self._outcomes: list[tuple[int, bool]] = []  # (ts, failed)
        self._probe_in_flight = False
        self._in_flight = 0  # allowed non-probe calls not yet recorded

    @property
    def state(self) -> BreakerState:
        """The stored state. Reading it never moves ``open`` to ``half_open``; only ``allow`` does."""
        with self._lock:
            return self._state

    @property
    def opened_at(self) -> int:
        with self._lock:
            return self._opened_at

    @property
    def probe_in_flight(self) -> bool:
        with self._lock:
            return self._probe_in_flight

    def allow(self, now: int) -> Allow:
        with self._lock:
            if self._state is BreakerState.CLOSED:
                self._in_flight += 1
                return Allow(True, False, self._state, None)
            if self._state is BreakerState.OPEN:
                if now - self._opened_at >= self._settings.open_ms:
                    self._state = BreakerState.HALF_OPEN
                    self._probe_in_flight = True
                    return Allow(True, True, self._state, PROBE)
                return Allow(False, False, self._state, None)
            if not self._probe_in_flight:
                self._probe_in_flight = True
                return Allow(True, True, self._state, None)
            return Allow(False, False, self._state, None)

    def record(self, outcome: Outcome, now: int, *, is_probe: bool) -> str | None:
        """Record the end of an allowed call that finished ``ok`` or with a failure."""
        if outcome is Outcome.BAD_REQUEST:
            raise BreakerError("bad_request is not a health outcome; use cancel()")
        failed = outcome.is_failure
        with self._lock:
            if is_probe:
                if not self._probe_in_flight:
                    raise BreakerError("record(probe) with no probe in flight")
                self._probe_in_flight = False
                if failed:
                    self._state = BreakerState.OPEN
                    self._opened_at = now
                    return BREAKER_OPENED
                self._state = BreakerState.CLOSED
                self._outcomes.clear()
                return BREAKER_CLOSED
            if self._in_flight <= 0:
                raise BreakerError("record() with no allowed call in flight")
            self._in_flight -= 1
            self._outcomes.append((now, failed))
            self._outcomes = [o for o in self._outcomes if o[0] > now - self._settings.window_ms]
            if self._state is not BreakerState.CLOSED:
                # Overlapping calls only (Phase 2): add the outcome, change nothing else.
                return None
            if failed:
                total = len(self._outcomes)
                failures = sum(1 for _, f in self._outcomes if f)
                if (
                    total >= self._settings.min_calls
                    and failures * 100 >= self._settings.failure_rate_pct * total
                ):
                    self._state = BreakerState.OPEN
                    self._opened_at = now
                    return BREAKER_OPENED
            return None

    def cancel(self, *, is_probe: bool) -> None:
        """Give back an allowed call that did not produce a health outcome (bad_request, budget refusal)."""
        with self._lock:
            if is_probe:
                if not self._probe_in_flight:
                    raise BreakerError("cancel(probe) with no probe in flight")
                self._probe_in_flight = False
                return
            if self._in_flight <= 0:
                raise BreakerError("cancel() with no allowed call in flight")
            self._in_flight -= 1


class RetryBudget:
    """Retries allowed as a share of first attempts in a sliding window (PRD 5.3)."""

    def __init__(self, settings: RetryBudgetSettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._first: list[int] = []
        self._retries: list[int] = []

    def note_first_attempt(self, ts: int) -> None:
        with self._lock:
            self._first.append(ts)

    def note_retry(self, ts: int) -> None:
        with self._lock:
            self._retries.append(ts)

    def counts(self, now: int) -> tuple[int, int, int]:
        """``(first_in_window, retries_in_window, allowed)``; "in window" means ``ts > now - window_ms``."""
        with self._lock:
            floor_ts = now - self._settings.window_ms
            first = sum(1 for t in self._first if t > floor_ts)
            retries = sum(1 for t in self._retries if t > floor_ts)
        allowed = max(self._settings.min_retries, first * self._settings.ratio_pct // 100)
        return first, retries, allowed

    def can_retry(self, now: int) -> bool:
        _, retries, allowed = self.counts(now)
        return retries < allowed


class Health:
    """One breaker and one retry budget per provider, created on first use."""

    def __init__(self, breaker: BreakerSettings, retry_budget: RetryBudgetSettings) -> None:
        self._breaker_settings = breaker
        self._retry_settings = retry_budget
        self._lock = threading.Lock()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._budgets: dict[str, RetryBudget] = {}

    def breaker(self, provider: str) -> CircuitBreaker:
        with self._lock:
            if provider not in self._breakers:
                self._breakers[provider] = CircuitBreaker(self._breaker_settings)
            return self._breakers[provider]

    def retry_budget(self, provider: str) -> RetryBudget:
        with self._lock:
            if provider not in self._budgets:
                self._budgets[provider] = RetryBudget(self._retry_settings)
            return self._budgets[provider]

    def states(self) -> dict[str, BreakerState]:
        """Stored breaker state per provider, in provider-id order."""
        with self._lock:
            items = sorted(self._breakers.items())
        return {provider: breaker.state for provider, breaker in items}
