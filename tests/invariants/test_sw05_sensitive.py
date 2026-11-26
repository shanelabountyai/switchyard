"""SW-05 / AC-D1: a tagged request never reaches an uncleared model and never touches the cache."""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings

from switchyard.cache import cache_key
from switchyard.models import CacheStatus, Request
from tests.helpers import catalog, engine, request, team
from tests.invariants.runs import Spec, run, specs


@settings(max_examples=80, deadline=None)
@given(specs())
def test_tagged_requests(spec: Spec) -> None:
    e = run(spec)
    stored = {(t, k) for t in e.teams for k in e.cache.keys(t)}
    for (_, raw, _), trace in zip(spec.requests, e.traces, strict=True):
        if not trace.data_tags:
            continue
        for attempt in trace.attempts:
            assert set(trace.data_tags) <= set(catalog().model(attempt.model).cleared_tags)
        assert trace.cache in (CacheStatus.BYPASS_SENSITIVE, CacheStatus.NOT_CHECKED)
        assert trace.cache_key == ""
        if trace.route is not None:
            # the key this request would have had is not in the cache (it was never written)
            key = cache_key(
                Request.model_validate(raw),
                trace.route.rule_id,
                trace.route.max_latency_ms,
                trace.route.max_cost_micro,
                e.config_version,
            )
            untagged_same_key = any(t.cache_key == key for t in e.traces if not t.data_tags)
            assert untagged_same_key or (trace.team, key) not in stored


def test_tagged_and_untagged_twins() -> None:
    """Same content, team and limits; only the tag differs. The tagged one never reads or writes the cache."""
    e = engine(teams={"research": team(cache_on=True)})
    shape: dict[str, Any] = {
        "team_name": "research",
        "app": "nb",
        "use_case": "notes",
        "task_class": "summarize",
    }
    tagged = e.handle(request("t1", data_tags=["pii"], **shape))
    assert e.cache.size("research") == 0
    plain = e.handle(request("p1", **shape))
    assert plain.cache is CacheStatus.MISS and e.cache.size("research") == 1
    again = e.handle(request("t2", data_tags=["pii"], **shape))
    assert (tagged.cache, again.cache) == (CacheStatus.BYPASS_SENSITIVE, CacheStatus.BYPASS_SENSITIVE)
    assert again.attempts and again.cost_micro > 0  # not served from the untagged entry
    assert e.cache.size("research") == 1
