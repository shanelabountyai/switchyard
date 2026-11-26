"""SW-08 / AC-A2, AC-A3: every decision's reasons are complete and rebuild its chain."""

from __future__ import annotations

from switchyard.routing import chain_from_reasons
from tests.helpers import CASES, case_result, catalog, policy


def test_golden_decisions_rebuild_from_reasons() -> None:
    checked = 0
    for path in CASES:
        for trace in case_result(path).traces:
            if trace.route is None:
                continue
            checked += 1
            assert chain_from_reasons(trace.route.reasons) == list(trace.route.chain)
            rule = next(r for r in policy().rules if r.id == trace.route.rule_id)
            considered = sorted(r.subject for r in trace.route.reasons if "tier" in r.detail)
            assert considered == sorted(m.id for m in catalog().models if m.tier in rule.tiers)
            codes = [r.code for r in trace.route.reasons]
            assert codes[:2] == ["rule_matched", "limits_applied"]
            assert codes[-1] == ("chosen" if trace.route.chain else "no_eligible_model")
    assert checked >= 25
