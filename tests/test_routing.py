from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from switchyard.canonical import canonical_json
from switchyard.models import Catalog, Request, RouteDecision, RoutingPolicy
from switchyard.routing import chain_from_reasons, route, worst_case
from tests.helpers import catalog, policy, request
from tests.strategies import catalogs, policies, requests


def _route(req: Request) -> RouteDecision:
    return route(req, policy(), catalog())


def _codes(decision: RouteDecision) -> list[str]:
    return [f"{r.code}:{r.subject}" for r in decision.reasons]


def test_we01_triage() -> None:
    d = _route(request())
    assert d.rule_id == "support-triage"
    assert d.chain == ("alder-small", "birch-small")
    costs = {r.subject: r.detail["worst_case_micro"] for r in d.reasons if r.code == "candidate"}
    assert costs == {"alder-small": 120, "birch-small": 150}


def test_we02_pii_excludes_uncleared() -> None:
    d = _route(
        request(
            team_name="marketing",
            app="site",
            use_case="blurb",
            task_class="summarize",
            input_tokens=1000,
            max_output_tokens=200,
            data_tags=["pii"],
        )
    )
    assert d.rule_id == "default"
    assert d.chain == ("alder-small", "alder-medium")
    assert "excluded_not_cleared:birch-small" in _codes(d)
    assert "excluded_not_cleared:birch-medium" in _codes(d)


def test_we03_cost_exclusions() -> None:
    d = _route(request(use_case="reply-draft", task_class="draft", input_tokens=3000, max_output_tokens=1500))
    assert d.chain == ("alder-medium",)
    assert _codes(d) == [
        "rule_matched:support-reply",
        "limits_applied:support-reply",
        "candidate:alder-medium",
        "excluded_cost:birch-medium",
        "excluded_cost:alder-large",
        "excluded_cost:cedar-large",
        "chosen:alder-medium",
    ]
    worst = {r.subject: r.detail["worst_case_micro"] for r in d.reasons if "tier" in r.detail}
    assert worst["alder-medium"] == 18_000 and worst["birch-medium"] == 22_500
    assert worst["alder-large"] == 108_000 and worst["cedar-large"] == 90_000


def test_we04_no_route() -> None:
    d = _route(
        request(
            team_name="legal",
            app="clm",
            use_case="contract-review",
            task_class="reason",
            input_tokens=20_000,
            max_output_tokens=2_000,
            data_tags=["restricted"],
            max_latency_ms=2000,
        )
    )
    assert d.chain == ()
    assert d.max_latency_ms == 2000
    assert _codes(d)[2:] == [
        "excluded_not_cleared:alder-large",
        "excluded_latency:cedar-large",
        "no_eligible_model:legal-review",
    ]


def test_we05_ceiling_rule() -> None:
    d = _route(
        request(
            team_name="marketing",
            app="site",
            use_case="blurb",
            task_class="summarize",
            input_tokens=1234,
            max_output_tokens=100,
        )
    )
    assert d.chain == ("alder-small", "birch-small", "alder-medium", "birch-medium")
    assert (
        worst_case(request(input_tokens=1234, max_output_tokens=100), catalog().model("alder-small")) == 327
    )


def test_first_matching_rule_wins_over_later_rules() -> None:
    # support/helpdesk/triage/classify also matches the catch-all; the earlier rule wins.
    assert _route(request()).rule_id == "support-triage"
    assert _route(request(app="other")).rule_id == "default"


def test_request_limit_can_only_tighten() -> None:
    loose = _route(request(max_latency_ms=99_999, max_cost_micro=99_999))
    assert (loose.max_latency_ms, loose.max_cost_micro) == (1000, 500)
    tight = _route(request(max_cost_micro=130))
    assert tight.max_cost_micro == 130
    assert tight.chain == ("alder-small",)


@given(requests(), policies(), catalogs())
def test_reasons_complete_and_rebuild_chain(req: Request, pol: RoutingPolicy, cat: Catalog) -> None:
    """AC-A2, AC-A3: every model of the rule's tiers appears once; the reasons alone rebuild the chain."""
    d = route(req, pol, cat)
    rule = pol.rules[0]
    in_tiers = sorted(m.id for m in cat.models if m.tier in rule.tiers)
    model_reasons = [r for r in d.reasons if "tier" in r.detail]
    assert sorted(r.subject for r in model_reasons) == in_tiers
    assert chain_from_reasons(d.reasons) == list(d.chain)
    # chain order: rule tier order, then worst-case cost, then id
    keys = [(rule.tiers.index(cat.model(m).tier), worst_case(req, cat.model(m)), m) for m in d.chain]
    assert keys == sorted(keys)


@given(requests(), policies(), catalogs())
def test_chain_never_holds_an_uncleared_model(req: Request, pol: RoutingPolicy, cat: Catalog) -> None:
    """AC-A4 / SW-05."""
    for model_id in route(req, pol, cat).chain:
        assert set(req.data_tags) <= set(cat.model(model_id).cleared_tags)


@given(requests(), policies(), catalogs(), st.integers(min_value=0, max_value=3000), st.integers(0, 100_000))
def test_tighter_request_limit_never_adds_a_candidate(
    req: Request, pol: RoutingPolicy, cat: Catalog, latency: int, cost: int
) -> None:
    """AC-A5."""
    base = route(req, pol, cat)

    def tighten(current: int | None, drawn: int) -> int:
        return drawn if current is None else min(current, drawn)

    tighter = req.model_copy(
        update={
            "max_latency_ms": tighten(req.max_latency_ms, latency),
            "max_cost_micro": tighten(req.max_cost_micro, cost),
        }
    )
    narrowed = route(tighter, pol, cat)
    assert set(narrowed.chain) <= set(base.chain)
    assert narrowed.max_latency_ms <= base.max_latency_ms
    assert narrowed.max_cost_micro <= base.max_cost_micro


@given(requests(), policies(), catalogs())
def test_routing_is_deterministic(req: Request, pol: RoutingPolicy, cat: Catalog) -> None:
    """AC-A1: byte-identical decisions for the same inputs."""
    first = canonical_json(route(req, pol, cat).model_dump(mode="json"))
    second = canonical_json(route(req, pol, cat).model_dump(mode="json"))
    assert first == second
