from __future__ import annotations

from typing import Any

from switchyard.engine import Engine
from switchyard.models import BreakerState, CacheStatus, Outcome, Request, RoutingPolicy, Status, Trace
from tests.helpers import START_MS, engine, policy, request, team, window


def _labels(trace: Trace) -> list[str]:
    return [e.label() for e in trace.events]


def _policy(**engine_changes: int) -> RoutingPolicy:
    data = policy().model_dump(mode="json")
    data["engine"].update(engine_changes)
    return RoutingPolicy.model_validate(data)


def _blurb(rid: str = "r1", **extra: Any) -> Request:
    return request(rid, "research", "nb", "notes", "summarize", 1000, 200, **extra)


def _no_open_holds(e: Engine) -> None:
    assert e.ledger.open_reserves() == ()


def _open_breaker(e: Engine, provider: str, at: int) -> None:
    breaker = e.health.breaker(provider)
    for _ in range(4):
        allow = breaker.allow(at)
        breaker.record(Outcome.SERVER_ERROR, at, is_probe=allow.is_probe)
    assert breaker.state is BreakerState.OPEN


def test_unknown_team() -> None:
    e = engine(teams={})
    trace = e.handle(request())
    assert (trace.status, trace.attempts, trace.cache, trace.route) == (
        Status.UNKNOWN_TEAM,
        (),
        CacheStatus.NOT_CHECKED,
        None,
    )
    assert e.ledger.entries() == ()


def test_duplicate_request_id_and_unknown_tag_are_bad_request() -> None:
    e = engine()
    assert e.handle(request("r1")).status is Status.OK
    dup = e.handle(request("r1"))
    assert (dup.status, dup.attempts, dup.cache) == (Status.BAD_REQUEST, (), CacheStatus.NOT_CHECKED)
    tagged = e.handle(request("r2", data_tags=["secret"]))
    assert (tagged.status, tagged.attempts) == (Status.BAD_REQUEST, ())
    assert len(e.ledger.entries()) == 2


def test_malformed_raw_request_is_bad_request() -> None:
    e = engine()
    for raw in (
        {"request_id": "x", "team": "support"},  # missing fields
        {**request("y").model_dump(mode="json"), "input_tokens": 1.5},  # float
        {**request("z").model_dump(mode="json"), "max_output_tokens": 0},
    ):
        trace = e.handle_raw(raw)
        assert (trace.status, trace.attempts, trace.cache) == (
            Status.BAD_REQUEST,
            (),
            CacheStatus.NOT_CHECKED,
        )
    assert e.ledger.entries() == ()


def test_no_route() -> None:
    e = engine()
    trace = e.handle(request(max_latency_ms=100))
    assert trace.status is Status.NO_ROUTE and trace.cache is CacheStatus.NOT_CHECKED


def test_failed_exhausted_when_every_breaker_is_open() -> None:
    e = engine()
    _open_breaker(e, "alder", START_MS)
    _open_breaker(e, "birch", START_MS)
    trace = e.handle(request())
    assert trace.status is Status.FAILED_EXHAUSTED
    assert trace.attempts == ()
    assert _labels(trace) == ["breaker_open_skip", "breaker_open_skip"]
    assert e.ledger.entries() == ()  # AC-B7: a skipped provider writes nothing


def test_failed_exhausted_at_max_attempts() -> None:
    e = engine(
        scripts={
            "alder": [window(0, 10**9, "server_error", latency_ms=10)],
            "birch": [window(0, 10**9, "rate_limited", latency_ms=10)],
        }
    )
    trace = e.handle(_blurb())
    assert trace.status is Status.FAILED_EXHAUSTED
    assert [a.model for a in trace.attempts] == ["alder-small", "alder-small", "birch-small"]
    assert _labels(trace) == ["retry", "no_retry:per_request_retry_limit", "no_retry:max_attempts"]
    assert trace.flags == ()
    _no_open_holds(e)


def test_rate_limited_counts_against_health_like_other_failures() -> None:
    e = engine(scripts={"alder": [window(0, 10**9, "rate_limited", latency_ms=10)]})
    traces = [e.handle(request(f"r{i}"), arrival_ms=START_MS + i * 1000) for i in range(3)]
    assert e.health.breaker("alder").state is BreakerState.OPEN  # 4 rate_limited outcomes of 4
    assert _labels(traces[0]) == ["retry", "no_retry:per_request_retry_limit"]
    assert all(t.status is Status.OK and t.attempts[-1].model == "birch-small" for t in traces)


def test_deny_on_a_fallback_attempt_keeps_earlier_charges() -> None:
    teams = {"support": team(soft=8000, hard=10_000)}
    e = engine(
        teams=teams,
        scripts={"alder": [window(0, 10**9, "server_error", latency_ms=10, billed_input_tokens=400)]},
    )
    e.ledger.adjust("support", 9800, START_MS, "illustrative opening")
    trace = e.handle(request())
    assert trace.status is Status.DENIED_BUDGET
    assert [(a.model, a.cost_micro) for a in trace.attempts] == [("alder-small", 80), ("alder-small", 80)]
    assert _labels(trace)[-1] == "budget_refused:150"
    assert e.ledger.balance("support", "2026-11").spent == 9960
    _no_open_holds(e)


def test_budget_refusal_during_probe_gives_the_permit_back() -> None:
    e = engine(teams={"support": team(soft=8000, hard=10_000)})
    _open_breaker(e, "alder", START_MS)
    e.ledger.adjust("support", 9950, START_MS, "illustrative opening")
    trace = e.handle(request(), arrival_ms=START_MS + 10_000)
    assert trace.status is Status.DENIED_BUDGET
    assert _labels(trace) == ["probe", "budget_refused:120"]
    breaker = e.health.breaker("alder")
    assert breaker.state is BreakerState.HALF_OPEN and not breaker.probe_in_flight
    assert breaker.allow(START_MS + 10_001).is_probe


def test_bad_request_during_probe_gives_the_permit_back() -> None:
    e = engine(scripts={"alder": [window(10_000, 10_100, "bad_request", latency_ms=5)]})
    _open_breaker(e, "alder", START_MS)
    trace = e.handle(request(), arrival_ms=START_MS + 10_000)
    assert trace.status is Status.BAD_REQUEST
    breaker = e.health.breaker("alder")
    assert breaker.state is BreakerState.HALF_OPEN and not breaker.probe_in_flight
    _no_open_holds(e)


def test_downgrade_chain_never_repeats_an_attempted_model() -> None:
    pol = _policy(max_retries_per_provider=0)
    teams = {"research": team(soft=8000, hard=10_000, overage="downgrade")}
    e = engine(
        teams=teams,
        the_policy=pol,
        scripts={
            "alder": [window(0, 10**9, "server_error", latency_ms=10)],
            "birch": [window(0, 10**9, "server_error", latency_ms=10)],
        },
    )
    e.ledger.adjust("research", 9000, START_MS, "illustrative opening")
    trace = e.handle(_blurb())
    assert [a.model for a in trace.attempts] == ["alder-small", "birch-small"]
    downgrade = [ev for ev in trace.events if ev.code == "downgrade_chain"]
    assert len(downgrade) == 1 and downgrade[0].detail["models"] == []
    assert trace.status is Status.DENIED_BUDGET
    assert "downgraded" not in trace.flags


def test_cache_hit_is_served_at_the_hard_limit() -> None:
    teams = {"research": team(soft=8000, hard=10_000, cache_on=True)}
    e = engine(teams=teams)
    e.ledger.adjust("research", 10_000 - 360, START_MS, "illustrative opening")
    first = e.handle(_blurb(), fake_output_tokens=150)
    assert first.status is Status.OK and first.cache is CacheStatus.MISS
    entries = len(e.ledger.entries())
    hit = e.handle(_blurb("r2"), arrival_ms=START_MS + 1000)
    assert (hit.status, hit.cache, hit.cost_micro, hit.avoided_micro) == (Status.OK, CacheStatus.HIT, 0, 320)
    assert len(e.ledger.entries()) == entries  # AC-D3


def test_answer_from_a_lower_tier_than_the_rules_first_is_not_stored() -> None:
    """AC-D6: an answer served from a later tier (here after outage fallback) is not cached."""
    pol = _policy(max_retries_per_provider=0, max_attempts=4)
    e = engine(
        teams={"research": team(cache_on=True)},
        the_policy=pol,
        scripts={
            "alder": [window(0, 1000, "server_error", latency_ms=10)],
            "birch": [window(0, 15, "server_error", latency_ms=10)],
        },
    )
    trace = e.handle(_blurb())
    assert [a.model for a in trace.attempts] == ["alder-small", "birch-small", "alder-medium", "birch-medium"]
    assert trace.status is Status.OK
    assert set(trace.flags) == {"fallback_used"}
    assert e.cache.size("research") == 0


def test_nondeterministic_temperature_bypasses() -> None:
    e = engine(teams={"research": team(cache_on=True)})
    trace = e.handle(_blurb(temperature="0.7"))
    assert trace.cache is CacheStatus.BYPASS_NONDETERMINISTIC and trace.cache_key == ""
    assert e.cache.size("research") == 0


def test_every_reserve_is_settled_before_handle_returns() -> None:
    e = engine(scripts={"alder": [window(0, 5000, "timeout")]})
    for i in range(5):
        e.handle(request(f"r{i}"), arrival_ms=START_MS + i * 1500)
        _no_open_holds(e)


def test_arrival_never_moves_the_clock_back() -> None:
    e = engine()
    e.handle(request("r1"), arrival_ms=START_MS + 1000)
    trace = e.handle(request("r2"), arrival_ms=START_MS)
    assert trace.arrived_ms == START_MS + 1400
