from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from switchyard.clock import FakeClock, day_of, format_utc, parse_utc, period_of


def test_month_and_day_boundaries() -> None:
    before = parse_utc("2026-11-30T23:59:59.999Z")
    after = parse_utc("2026-12-01T00:00:00.000Z")
    assert after - before == 1
    assert (period_of(before), day_of(before)) == ("2026-11", "2026-11-30")
    assert (period_of(after), day_of(after)) == ("2026-12", "2026-12-01")


def test_leap_day() -> None:
    ms = parse_utc("2028-02-29T12:00:00.000Z")
    assert day_of(ms) == "2028-02-29"
    assert day_of(ms + 12 * 3_600_000) == "2028-03-01"


@given(st.integers(min_value=0, max_value=4_102_444_800_000))
def test_parse_format_round_trip(ms: int) -> None:
    assert parse_utc(format_utc(ms)) == ms


def test_parse_requires_utc() -> None:
    with pytest.raises(ValueError):
        parse_utc("2026-11-02T09:00:00.000+01:00")
    with pytest.raises(ValueError):
        parse_utc("2026-11-02T09:00:00.000")


def test_fake_clock_only_moves_forward() -> None:
    clock = FakeClock(100)
    clock.advance(0)
    clock.advance(50)
    assert clock.now_ms() == 150
    with pytest.raises(ValueError):
        clock.advance(-1)
    with pytest.raises(TypeError):
        clock.advance(1.5)  # type: ignore[arg-type]
    assert clock.now_ms() == 150
