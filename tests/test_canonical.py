from __future__ import annotations

import pytest

from switchyard.canonical import canonical_json, content_hash


def test_key_order_does_not_change_hash() -> None:
    assert content_hash({"a": 1, "b": [1, 2]}) == content_hash({"b": [1, 2], "a": 1})
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'


def test_floats_rejected() -> None:
    with pytest.raises(TypeError):
        canonical_json({"a": [1, 0.5]})


def test_hash_prefix() -> None:
    assert content_hash([]).startswith("sha256:")
    assert len(content_hash([])) == len("sha256:") + 64
