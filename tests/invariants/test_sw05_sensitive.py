"""SW-05 / AC-D1: a tagged request never reaches an uncleared model and never touches the cache."""

from __future__ import annotations

from hypothesis import given, settings

from switchyard.models import CacheStatus, Status
from tests.helpers import catalog
from tests.invariants.runs import Spec, run, specs


@settings(max_examples=80, deadline=None)
@given(specs())
def test_tagged_requests(spec: Spec) -> None:
    e = run(spec)
    for trace in e.traces:
        if not trace.data_tags:
            continue
        for attempt in trace.attempts:
            assert set(trace.data_tags) <= set(catalog().model(attempt.model).cleared_tags)
        assert trace.cache in (CacheStatus.BYPASS_SENSITIVE, CacheStatus.NOT_CHECKED)
        assert trace.cache_key == ""
    # nothing in the cache came from a tagged request: every stored entry's key belongs to an untagged miss
    stored_keys = {t.cache_key for t in e.traces if t.cache is CacheStatus.MISS and t.status is Status.OK}
    tagged_keys = {t.cache_key for t in e.traces if t.data_tags}
    assert not (stored_keys & tagged_keys - {""})
