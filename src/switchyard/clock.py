"""Injected time (PRD 3.2). The only module in ``src/`` allowed to read real time."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MS = timedelta(milliseconds=1)


class Clock(Protocol):
    def now_ms(self) -> int: ...

    def advance(self, ms: int) -> None: ...


class FakeClock:
    """A clock that only moves when told to. Never goes backwards."""

    def __init__(self, start_ms: int) -> None:
        if isinstance(start_ms, bool) or not isinstance(start_ms, int):
            raise TypeError("start_ms must be an int")
        self._now = start_ms
        self._lock = threading.Lock()

    def now_ms(self) -> int:
        with self._lock:
            return self._now

    def advance(self, ms: int) -> None:
        if isinstance(ms, bool) or not isinstance(ms, int):
            raise TypeError("ms must be an int")
        if ms < 0:
            raise ValueError(f"a clock cannot go backwards (advance {ms})")
        with self._lock:
            self._now += ms


class SystemClock:
    """Real time, for Phase 2. Not used by any Phase 1 test or case."""

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def advance(self, ms: int) -> None:
        if ms < 0:
            raise ValueError(f"a clock cannot go backwards (advance {ms})")
        time.sleep(timedelta(milliseconds=ms).total_seconds())


def to_datetime(ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=ms)


def parse_utc(text: str) -> int:
    """``"2026-11-30T23:59:59.000Z"`` -> milliseconds since the epoch."""
    if not text.endswith("Z"):
        raise ValueError(f"timestamp must be UTC and end in 'Z': {text!r}")
    parsed = datetime.fromisoformat(text)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"timestamp must be UTC: {text!r}")
    return (parsed - _EPOCH) // _ONE_MS


def format_utc(ms: int) -> str:
    """Milliseconds since the epoch -> ISO 8601 with milliseconds and ``Z``."""
    return to_datetime(ms).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def period_of(ms: int) -> str:
    """UTC calendar month ``YYYY-MM`` containing ``ms``."""
    return to_datetime(ms).strftime("%Y-%m")


def day_of(ms: int) -> str:
    """UTC day ``YYYY-MM-DD`` containing ``ms``."""
    return to_datetime(ms).strftime("%Y-%m-%d")
