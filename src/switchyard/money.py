"""Integer micro-dollar arithmetic (PRD 3.1).

1 USD = 1,000,000 µUSD. Prices are µUSD per 1,000,000 tokens. Every value here is an ``int``; no float touches
a money value anywhere in this package.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import Field, Strict

MICRO_PER_USD = 1_000_000
TOKENS_PER_PRICE_UNIT = 1_000_000

# Strict ints reject float, bool and numeric strings at the model boundary (AC-C7).
Micro = Annotated[int, Strict(), Field(ge=0)]
SignedMicro = Annotated[int, Strict()]

_USD_RE = re.compile(r"^(-?)(0|[1-9][0-9]*)\.([0-9]{6})$")


def require_int(name: str, value: object) -> int:
    """Return ``value`` if it is a real ``int`` (not ``bool``); raise ``TypeError`` otherwise."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def ceil_div(numerator: int, denominator: int) -> int:
    """Integer ceiling division for a non-negative numerator and positive denominator."""
    return -(-numerator // denominator)


def cost_micro(input_tokens: int, output_tokens: int, input_price: int, output_price: int) -> int:
    """Cost of one attempt: one ceiling over the sum of input and output (PRD 3.1)."""
    values = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_price": input_price,
        "output_price": output_price,
    }
    for name, value in values.items():
        if require_int(name, value) < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")
    return ceil_div(input_tokens * input_price + output_tokens * output_price, TOKENS_PER_PRICE_UNIT)


def format_usd(micro: int) -> str:
    """``13200`` -> ``"0.013200"``; a negative amount gets a leading minus sign."""
    require_int("micro", micro)
    sign = "-" if micro < 0 else ""
    whole, frac = divmod(abs(micro), MICRO_PER_USD)
    return f"{sign}{whole}.{frac:06d}"


def parse_usd(text: str) -> int:
    """Exact inverse of :func:`format_usd`. Rejects any other spelling."""
    match = _USD_RE.match(text)
    if match is None or text == "-0.000000":
        raise ValueError(f"not a six-decimal USD amount: {text!r}")
    sign, whole, frac = match.groups()
    value = int(whole) * MICRO_PER_USD + int(frac)
    return -value if sign else value
