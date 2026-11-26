"""Canonical JSON and content hashes, the same rule as Yardstick's ``hashing.py``. Floats are rejected."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise TypeError(f"float at {path} is not allowed in canonical JSON")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_floats(item, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _reject_floats(item, f"{path}[{index}]")


def canonical_json(value: Any) -> str:
    """Sorted keys, no extra whitespace, UTF-8 kept as-is, no NaN, no float."""
    _reject_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_hash(value: Any) -> str:
    """``sha256:<hex>`` of the canonical JSON form of ``value``."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
