"""Budget checks and the downgrade chain (PRD 6.2, 6.3). Pure functions."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from switchyard.models import (
    OVER_HARD_LIMIT,
    SOFT_LIMIT_CROSSED,
    TIERS_LOW_TO_HIGH,
    Catalog,
    ModelSpec,
    Request,
    RouteRule,
    TeamBudget,
    Tier,
)
from switchyard.routing import effective_limits, eligible


@dataclass(frozen=True)
class BudgetCheck:
    accepted: bool
    flags: tuple[str, ...]
    ceiling: int


def ceiling(budget: TeamBudget) -> int:
    """``hard_limit + overage_cap`` under ``allow_with_flag``, else ``hard_limit``."""
    return budget.ceiling


def check(committed: int, amount: int, budget: TeamBudget) -> BudgetCheck:
    """Refuse exactly when ``committed + amount > ceiling``. Flags describe the hold (PRD 6.2)."""
    after = committed + amount
    top = budget.ceiling
    if after > top:
        return BudgetCheck(accepted=False, flags=(), ceiling=top)
    flags: list[str] = []
    if after > budget.hard_limit_micro:
        flags.append(OVER_HARD_LIMIT)
    if after > budget.soft_limit_micro:
        flags.append(SOFT_LIMIT_CROSSED)
    return BudgetCheck(accepted=True, flags=tuple(sorted(flags)), ceiling=top)


def downgrade_tiers(refused_tier: Tier, min_tier: Tier) -> list[Tier]:
    """Tiers strictly below the refused tier and not below ``min_tier``, highest first (global order)."""
    return [t for t in reversed(TIERS_LOW_TO_HIGH) if min_tier.rank <= t.rank < refused_tier.rank]


def downgrade_chain(
    request: Request,
    rule: RouteRule,
    catalog: Catalog,
    refused_tier: Tier,
    exclude: Collection[str] = (),
) -> list[ModelSpec]:
    """Every eligible model in the downgrade tiers, cheapest first within a tier, minus ``exclude``."""
    latency, cost = effective_limits(request, rule)
    chain, _ = eligible(request, catalog, downgrade_tiers(refused_tier, rule.min_tier), latency, cost)
    return [m for m in chain if m.id not in exclude]
