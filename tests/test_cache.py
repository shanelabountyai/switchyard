from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from switchyard.cache import CacheEntry, CacheRefused, ExactCache, bypass_reason, cache_key, normalize
from switchyard.models import CacheStatus, Message, Request
from tests.helpers import request, team

ENTRY = CacheEntry(stored_at=400, model="alder-small", cost_micro=320, text="t")


def _key(req: Request, rule: str = "default", latency: int = 2000, cost: int = 5000, cv: str = "cv") -> str:
    return cache_key(req, rule, latency, cost, cv)


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("hello", "hello  ", True),
        ("  hello\n", "hello", True),
        ("line1\r\nline2", "line1\nline2", True),
        ("line1\rline2", "line1\nline2", True),
        ("café", "café", True),  # NFC
        ("hello world", "hello  world", False),  # inner whitespace kept
        ("Hello", "hello", False),  # no case folding
    ],
)
def test_normalization_table(a: str, b: str, same: bool) -> None:
    assert (_key(request(content=a)) == _key(request(content=b))) is same


def test_role_is_lower_cased() -> None:
    assert normalize([Message(role="USER", content=" x ")]) == [{"role": "user", "content": "x"}]


def test_key_ignores_request_id_and_app_only() -> None:
    """AC-D2."""
    base = request()
    assert _key(base) == _key(request(request_id="other", app="other-app"))
    for changed in (
        request(team_name="marketing"),
        request(use_case="x"),
        request(task_class="x"),
        request(input_tokens=401),
        request(max_output_tokens=51),
        request(temperature="0.5"),
        request(content="other"),
    ):
        assert _key(changed) != _key(base)
    assert _key(base, rule="other") != _key(base)
    assert _key(base, latency=1) != _key(base)
    assert _key(base, cost=1) != _key(base)
    assert _key(base, cv="cv2") != _key(base)


@given(st.text(max_size=12), st.text(max_size=12))
def test_key_equal_exactly_when_normalized_equal(a: str, b: str) -> None:
    ra, rb = request(content=a), request(content=b)
    assert (_key(ra) == _key(rb)) == (normalize(ra.messages) == normalize(rb.messages))


def test_bypass_order() -> None:
    on, off = team(cache_on=True).cache, team(cache_on=False).cache
    assert bypass_reason(request(data_tags=["pii"], temperature="1"), off) is CacheStatus.BYPASS_SENSITIVE
    assert bypass_reason(request(temperature="1"), off) is CacheStatus.BYPASS_TEAM_OPT_OUT
    assert bypass_reason(request(temperature="1"), on) is CacheStatus.BYPASS_NONDETERMINISTIC
    assert bypass_reason(request(temperature="0.0"), on) is CacheStatus.BYPASS_NONDETERMINISTIC
    assert bypass_reason(request(), on) is None


def test_ttl_boundary() -> None:
    """AC-D4: a hit at ttl - 1, a miss at ttl."""
    cache = ExactCache(10)
    cache.put("research", "k", ENTRY, request(), team(cache_on=True).cache)
    assert cache.get("research", "k", 400 + 59_999, 60_000) == ENTRY
    assert cache.get("research", "k", 400 + 60_000, 60_000) is None
    assert cache.size("research") == 0


def test_scope_is_one_team() -> None:
    cache = ExactCache(10)
    cache.put("research", "k", ENTRY, request(), team(cache_on=True).cache)
    assert cache.get("support", "k", 400, 60_000) is None


def test_put_refuses_bypassed_requests() -> None:
    cache = ExactCache(10)
    with pytest.raises(CacheRefused):
        cache.put("t", "k", ENTRY, request(data_tags=["pii"]), team(cache_on=True).cache)
    with pytest.raises(CacheRefused):
        cache.put("t", "k", ENTRY, request(), team(cache_on=False).cache)
    assert cache.size("t") == 0


def test_eviction_drops_oldest_then_by_key() -> None:
    cache = ExactCache(2)
    tc = team(cache_on=True).cache
    cache.put("t", "b", CacheEntry(10, "m", 1, ""), request(), tc)
    cache.put("t", "a", CacheEntry(10, "m", 1, ""), request(), tc)
    cache.put("t", "c", CacheEntry(20, "m", 1, ""), request(), tc)
    assert cache.get("t", "a", 20, 100) is None  # same stored_at as "b"; "a" sorts first
    assert cache.get("t", "b", 20, 100) is not None
    assert cache.size("t") == 2
