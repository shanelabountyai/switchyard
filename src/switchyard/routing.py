"""Routing policy as data (PRD 4.1). A pure function of ``(request, policy, catalog)``."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from switchyard.models import (
    WILDCARD,
    Catalog,
    ModelSpec,
    Reason,
    Request,
    RouteDecision,
    RouteRule,
    RoutingPolicy,
    Tier,
)
from switchyard.money import cost_micro

CANDIDATE = "candidate"
EXCLUDED_NOT_CLEARED = "excluded_not_cleared"
EXCLUDED_LATENCY = "excluded_latency"
EXCLUDED_COST = "excluded_cost"


def worst_case(request: Request, model: ModelSpec) -> int:
    """Cost of one attempt if the model returns ``max_output_tokens`` (PRD 3.1)."""
    return cost_micro(
        request.input_tokens,
        request.max_output_tokens,
        model.input_price_micro_per_1m,
        model.output_price_micro_per_1m,
    )


def _matches(matcher: str, value: str) -> bool:
    return matcher in (WILDCARD, value)


def match_rule(request: Request, policy: RoutingPolicy) -> RouteRule:
    """The first rule in file order whose four matchers all match. The catch-all guarantees one."""
    for rule in policy.rules:
        if (
            _matches(rule.team, request.team)
            and _matches(rule.app, request.app)
            and _matches(rule.use_case, request.use_case)
            and _matches(rule.task_class, request.task_class)
        ):
            return rule
    raise AssertionError("a loaded policy always ends with a catch-all rule")


def effective_limits(request: Request, rule: RouteRule) -> tuple[int, int]:
    """``(max_latency_ms, max_cost_micro)``: a request may only tighten the rule's limits."""
    latency = (
        rule.max_latency_ms
        if request.max_latency_ms is None
        else min(rule.max_latency_ms, request.max_latency_ms)
    )
    cost = (
        rule.max_cost_micro
        if request.max_cost_micro is None
        else min(rule.max_cost_micro, request.max_cost_micro)
    )
    return latency, cost


def eligible(
    request: Request,
    catalog: Catalog,
    tiers: Sequence[Tier],
    max_latency_ms: int,
    max_cost_micro: int,
) -> tuple[list[ModelSpec], list[Reason]]:
    """Candidates in chain order, and one reason per model considered (PRD 4.1).

    Shared by routing and the budget downgrade chain, so both apply the same three checks.
    """
    chain: list[ModelSpec] = []
    reasons: list[Reason] = []
    tags = set(request.data_tags)
    for tier in tiers:
        tier_candidates: list[tuple[int, str, ModelSpec]] = []
        for model in sorted((m for m in catalog.models if m.tier is tier), key=lambda m: m.id):
            cost = worst_case(request, model)
            if not tags <= set(model.cleared_tags):
                code = EXCLUDED_NOT_CLEARED
            elif model.declared_latency_ms > max_latency_ms:
                code = EXCLUDED_LATENCY
            elif cost > max_cost_micro:
                code = EXCLUDED_COST
            else:
                code = CANDIDATE
                tier_candidates.append((cost, model.id, model))
            reasons.append(
                Reason(
                    code=code,
                    subject=model.id,
                    detail={
                        "tier": tier.value,
                        "worst_case_micro": cost,
                        "declared_latency_ms": model.declared_latency_ms,
                    },
                )
            )
        tier_candidates.sort(key=lambda c: (c[0], c[1]))
        chain.extend(c[2] for c in tier_candidates)
    return chain, reasons


def route(request: Request, policy: RoutingPolicy, catalog: Catalog) -> RouteDecision:
    """Match a rule, apply limits, filter and order the models, and explain every step."""
    rule = match_rule(request, policy)
    latency, cost = effective_limits(request, rule)
    chain, model_reasons = eligible(request, catalog, rule.tiers, latency, cost)
    reasons = [
        Reason(code="rule_matched", subject=rule.id),
        Reason(
            code="limits_applied", subject=rule.id, detail={"max_latency_ms": latency, "max_cost_micro": cost}
        ),
        *model_reasons,
    ]
    if chain:
        reasons.append(Reason(code="chosen", subject=chain[0].id))
    else:
        reasons.append(Reason(code="no_eligible_model", subject=rule.id))
    return RouteDecision(
        rule_id=rule.id,
        max_latency_ms=latency,
        max_cost_micro=cost,
        chain=tuple(m.id for m in chain),
        reasons=tuple(reasons),
    )


def chain_from_reasons(reasons: Iterable[Reason]) -> list[str]:
    """Rebuild the chain from the reasons alone (AC-A3, SW-08).

    Candidates are grouped by tier in the order tiers first appear, then ordered by worst-case cost and id.
    """
    tier_order: list[str] = []
    by_tier: dict[str, list[tuple[int, str]]] = {}
    for reason in reasons:
        tier = reason.detail.get("tier")
        if not isinstance(tier, str):
            continue
        if tier not in by_tier:
            tier_order.append(tier)
            by_tier[tier] = []
        if reason.code == CANDIDATE:
            cost = reason.detail["worst_case_micro"]
            assert isinstance(cost, int)
            by_tier[tier].append((cost, reason.subject))
    return [model_id for tier in tier_order for _, model_id in sorted(by_tier[tier])]
