"""Hypothesis strategies for random catalogs, policies and requests (all illustrative)."""

from __future__ import annotations

from typing import Any

from hypothesis import strategies as st

from switchyard.models import Catalog, Request, RouteRule, RoutingPolicy, Tier
from tests.helpers import policy

TAGS = ["pii", "restricted"]
TIERS = [t.value for t in Tier]


@st.composite
def catalogs(draw: st.DrawFn) -> Catalog:
    count = draw(st.integers(min_value=1, max_value=7))
    models: list[dict[str, Any]] = []
    for i in range(count):
        models.append(
            {
                "id": f"m{i}",
                "provider": draw(st.sampled_from(["alder", "birch", "cedar"])),
                "tier": draw(st.sampled_from(TIERS)),
                "input_price_micro_per_1m": draw(st.integers(min_value=0, max_value=20_000_000)),
                "output_price_micro_per_1m": draw(st.integers(min_value=0, max_value=60_000_000)),
                "declared_latency_ms": draw(st.integers(min_value=0, max_value=3_000)),
                "cleared_tags": draw(st.lists(st.sampled_from(TAGS), unique=True)),
            }
        )
    return Catalog.model_validate({"illustrative": True, "known_tags": TAGS, "models": models})


@st.composite
def rules(draw: st.DrawFn, rule_id: str = "rule") -> RouteRule:
    tiers = draw(st.lists(st.sampled_from(TIERS), min_size=1, max_size=3, unique=True))
    min_rank = draw(st.integers(min_value=0, max_value=Tier(tiers[0]).rank))
    return RouteRule.model_validate(
        {
            "id": rule_id,
            "team": "*",
            "app": "*",
            "use_case": "*",
            "task_class": "*",
            "tiers": tiers,
            "max_latency_ms": draw(st.integers(min_value=0, max_value=3_000)),
            "max_cost_micro": draw(st.integers(min_value=0, max_value=100_000)),
            "min_tier": TIERS[min_rank],
        }
    )


@st.composite
def policies(draw: st.DrawFn) -> RoutingPolicy:
    data = policy().model_dump(mode="json")
    data["rules"] = [draw(rules()).model_dump(mode="json")]
    return RoutingPolicy.model_validate(data)


@st.composite
def requests(draw: st.DrawFn, request_id: str = "r", team: str = "t") -> Request:
    return Request.model_validate(
        {
            "request_id": request_id,
            "team": team,
            "app": "a",
            "use_case": "u",
            "task_class": "c",
            "data_tags": draw(st.lists(st.sampled_from(TAGS), unique=True, max_size=2)),
            "messages": [{"role": "user", "content": draw(st.text(max_size=20))}],
            "input_tokens": draw(st.integers(min_value=0, max_value=5_000)),
            "max_output_tokens": draw(st.integers(min_value=1, max_value=2_000)),
            "max_latency_ms": draw(st.none() | st.integers(min_value=0, max_value=3_000)),
            "max_cost_micro": draw(st.none() | st.integers(min_value=0, max_value=100_000)),
        }
    )
