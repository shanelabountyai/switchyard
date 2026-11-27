from __future__ import annotations

import pytest

from switchyard.budget import check, downgrade_chain, downgrade_tiers
from switchyard.models import RouteRule, Tier
from tests.helpers import catalog, policy, request, team


@pytest.mark.parametrize(
    ("committed", "amount", "accepted", "flags"),
    [
        (7_999, 0, True, ()),
        (7_900, 100, True, ()),  # exactly the soft limit does not flag
        (7_900, 101, True, ("soft_limit_crossed",)),
        (9_999, 1, True, ("soft_limit_crossed",)),  # exactly the hard limit
        (9_999, 2, False, ()),
    ],
)
def test_deny_boundaries(committed: int, amount: int, accepted: bool, flags: tuple[str, ...]) -> None:
    result = check(committed, amount, team(soft=8000, hard=10_000).budget)
    assert (result.accepted, result.flags, result.ceiling) == (accepted, flags, 10_000)


@pytest.mark.parametrize(
    ("after", "accepted", "flags"),
    [
        (10_000, True, ("soft_limit_crossed",)),
        (10_001, True, ("over_hard_limit", "soft_limit_crossed")),
        (12_000, True, ("over_hard_limit", "soft_limit_crossed")),
        (12_001, False, ()),
    ],
)
def test_allow_with_flag_boundaries(after: int, accepted: bool, flags: tuple[str, ...]) -> None:
    result = check(after - 100, 100, team(soft=8000, hard=10_000, overage="allow_with_flag", cap=2000).budget)
    assert (result.accepted, result.flags, result.ceiling) == (accepted, flags, 12_000)


def _rule(rule_id: str) -> RouteRule:
    return next(r for r in policy().rules if r.id == rule_id)


def test_we18_downgrade_chain() -> None:
    req = request(use_case="reply-draft", task_class="draft", input_tokens=800, max_output_tokens=400)
    chain = downgrade_chain(req, _rule("support-reply"), catalog(), Tier.STANDARD)
    assert [m.id for m in chain] == ["alder-small", "birch-small"]


def test_downgrade_from_premium_goes_highest_first() -> None:
    assert downgrade_tiers(Tier.PREMIUM, Tier.ECONOMY) == [Tier.STANDARD, Tier.ECONOMY]
    assert downgrade_tiers(Tier.PREMIUM, Tier.STANDARD) == [Tier.STANDARD]
    assert downgrade_tiers(Tier.ECONOMY, Tier.ECONOMY) == []


def test_min_tier_premium_gives_empty_downgrade_chain() -> None:
    req = request(
        team_name="legal",
        app="clm",
        use_case="contract-review",
        task_class="reason",
        input_tokens=2000,
        max_output_tokens=500,
    )
    assert downgrade_chain(req, _rule("legal-review"), catalog(), Tier.PREMIUM) == []


def test_downgrade_chain_excludes_attempted_and_keeps_clearance() -> None:
    req = request(
        use_case="reply-draft", task_class="draft", input_tokens=800, max_output_tokens=400, data_tags=["pii"]
    )
    chain = downgrade_chain(req, _rule("support-reply"), catalog(), Tier.STANDARD, exclude={"alder-small"})
    assert chain == []  # birch-small is not cleared for pii, alder-small already attempted
