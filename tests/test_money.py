from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from switchyard.money import cost_micro, format_usd, parse_usd

tokens = st.integers(min_value=0, max_value=10_000_000)
prices = st.integers(min_value=0, max_value=100_000_000)


@pytest.mark.parametrize(
    ("inp", "out", "ip", "op", "expected"),
    [
        (1234, 57, 200_000, 800_000, 293),  # exact 292.4 (WE-05)
        (1234, 100, 200_000, 800_000, 327),  # exact 326.8 (WE-05)
        (0, 0, 200_000, 800_000, 0),
        (3000, 900, 2_000_000, 8_000_000, 13_200),  # WE-03, exact
        (400, 12, 250_000, 1_000_000, 112),  # exact 112
        (400, 12, 200_000, 800_000, 90),  # exact 89.6
        (1, 0, 1, 0, 1),  # 0.000001 rounds up to 1
    ],
)
def test_cost_table(inp: int, out: int, ip: int, op: int, expected: int) -> None:
    assert cost_micro(inp, out, ip, op) == expected


@given(tokens, tokens, prices, prices)
def test_ceiling_bound(inp: int, out: int, ip: int, op: int) -> None:
    cost = cost_micro(inp, out, ip, op)
    exact = inp * ip + out * op
    assert isinstance(cost, int)
    assert cost * 1_000_000 >= exact > (cost - 1) * 1_000_000 or (exact == 0 and cost == 0)


@given(tokens, tokens, tokens, prices, prices)
def test_monotonic_in_tokens(inp: int, out: int, extra: int, ip: int, op: int) -> None:
    assert cost_micro(inp + extra, out, ip, op) >= cost_micro(inp, out, ip, op)
    assert cost_micro(inp, out + extra, ip, op) >= cost_micro(inp, out, ip, op)


@given(tokens, tokens, tokens, tokens, prices, prices)
def test_subadditive(a_in: int, a_out: int, b_in: int, b_out: int, ip: int, op: int) -> None:
    assert cost_micro(a_in + b_in, a_out + b_out, ip, op) <= cost_micro(a_in, a_out, ip, op) + cost_micro(
        b_in, b_out, ip, op
    )


@pytest.mark.parametrize("bad", [1.0, True, "1", None])
def test_non_int_inputs_raise(bad: object) -> None:
    with pytest.raises(TypeError):
        cost_micro(bad, 0, 1, 1)  # type: ignore[arg-type]


def test_negative_tokens_raise() -> None:
    with pytest.raises(ValueError):
        cost_micro(-1, 0, 1, 1)


def test_format_usd() -> None:
    assert format_usd(13_200) == "0.013200"
    assert format_usd(650) == "0.000650"
    assert format_usd(0) == "0.000000"
    assert format_usd(-13_200) == "-0.013200"
    assert format_usd(12_345_678) == "12.345678"


@given(st.integers(min_value=-(10**15), max_value=10**15))
def test_format_parse_round_trip(micro: int) -> None:
    assert parse_usd(format_usd(micro)) == micro


@pytest.mark.parametrize("bad", ["1.5", "0.0000001", "-0.000000", "01.000000", "1e3", "", " 0.000001"])
def test_parse_usd_rejects_other_spellings(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_usd(bad)


def test_format_usd_rejects_float() -> None:
    with pytest.raises(TypeError):
        format_usd(0.5)  # type: ignore[arg-type]
