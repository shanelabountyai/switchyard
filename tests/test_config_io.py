"""AC-A6: a bad policy or catalog fails to load with one clear error naming the file and field."""

from __future__ import annotations

from pathlib import Path

import pytest

from switchyard.config_io import ConfigError, config_version, load_catalog, load_policy, load_teams
from switchyard.models import Catalog, RoutingPolicy, TeamBudget, TeamConfig
from tests.helpers import FIXTURES, catalog, policy

POLICY = (FIXTURES / "policy" / "illustrative-policy.yaml").read_text()
CATALOG = (FIXTURES / "catalog" / "illustrative-catalog.yaml").read_text()


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_illustrative_files_load() -> None:
    assert [m.id for m in catalog().models][:2] == ["alder-small", "birch-small"]
    assert [r.id for r in policy().rules] == ["support-triage", "support-reply", "legal-review", "default"]
    assert policy().engine.max_attempts == 3


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        (
            '  - {id: default, team: "*", app: "*", use_case: "*", task_class: "*",',
            '  - {id: default, team: "*", app: "*", use_case: "*", task_class: other,',
            "catch-all",
        ),
        (
            "tiers: [economy], max_latency_ms: 1000",
            "tiers: [budget], max_latency_ms: 1000",
            "rules.0.tiers.0",
        ),
        ("id: support-reply,", "id: support-triage,", "duplicate rule id"),
        (
            "max_latency_ms: 1000, max_cost_micro: 500",
            "max_latency_ms: -1, max_cost_micro: 500",
            "max_latency_ms",
        ),
        ("max_cost_micro: 500,", "max_cost_micro: 500.0,", "max_cost_micro"),
        ("backoff_ms: 200", "backoff_ms: 200.5", "engine.backoff_ms"),
        ("max_attempts: 3", "max_attempts: true", "engine.max_attempts"),
        ("max_cost_micro: 500,", 'max_cost_micro: "500",', "max_cost_micro"),
        ("min_tier: premium}", "min_tier: premium, extra: 1}", "extra"),
    ],
)
def test_bad_policy_fails_with_one_error(tmp_path: Path, old: str, new: str, needle: str) -> None:
    assert old in POLICY
    path = _write(tmp_path, "policy.yaml", POLICY.replace(old, new, 1))
    with pytest.raises(ConfigError) as info:
        load_policy(path)
    message = str(info.value)
    assert str(path) in message
    assert needle in message
    assert "\n" not in message


def test_min_tier_above_first_tier_fails(tmp_path: Path) -> None:
    text = POLICY.replace(
        "tiers: [economy], max_latency_ms: 1000, max_cost_micro: 500, min_tier: economy",
        "tiers: [economy], max_latency_ms: 1000, max_cost_micro: 500, min_tier: standard",
    )
    with pytest.raises(ConfigError, match="min_tier"):
        load_policy(_write(tmp_path, "p.yaml", text))


def test_duplicate_yaml_key_fails(tmp_path: Path) -> None:
    text = POLICY.replace("  max_attempts: 3\n", "  max_attempts: 3\n  max_attempts: 4\n")
    with pytest.raises(ConfigError, match="duplicate key"):
        load_policy(_write(tmp_path, "p.yaml", text))


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        ("id: birch-small,", "id: alder-small,", "duplicate model id"),
        ("cleared_tags: [pii, restricted]}", "cleared_tags: [pii, secret]}", "unknown tag"),
        (
            "tier: economy, input_price_micro_per_1m: 200000",
            "tier: cheap, input_price_micro_per_1m: 200000",
            "models.0.tier",
        ),
        ("input_price_micro_per_1m: 200000,", "input_price_micro_per_1m: 0.2,", "input_price_micro_per_1m"),
        ("declared_latency_ms: 400,", "declared_latency_ms: -400,", "declared_latency_ms"),
    ],
)
def test_bad_catalog_fails_with_one_error(tmp_path: Path, old: str, new: str, needle: str) -> None:
    assert old in CATALOG
    path = _write(tmp_path, "catalog.yaml", CATALOG.replace(old, new, 1))
    with pytest.raises(ConfigError) as info:
        load_catalog(path)
    assert needle in str(info.value)
    assert str(path) in str(info.value)


def test_team_budget_rules() -> None:
    base = {
        "period": "calendar_month_utc",
        "soft_limit_micro": 10,
        "hard_limit_micro": 20,
        "overage": "deny",
        "overage_cap_micro": 0,
    }
    TeamBudget.model_validate(base)
    for bad in (
        {"soft_limit_micro": 30},
        {"overage": "allow_with_flag"},  # needs a cap
        {"overage_cap_micro": 5},  # cap only with allow_with_flag
        {"hard_limit_micro": 20.0},
        {"hard_limit_micro": True},
        {"period": "rolling_30_days"},
    ):
        with pytest.raises(ValueError):
            TeamBudget.model_validate({**base, **bad})
    with pytest.raises(ConfigError, match=r"teams\.support"):
        load_teams(
            {
                "support": {
                    "budget": {**base, "hard_limit_micro": "20"},
                    "cache": {"enabled": False, "ttl_ms": 1},
                }
            }
        )
    assert isinstance(
        load_teams({"a": {"budget": base, "cache": {"enabled": True, "ttl_ms": 1}}})["a"], TeamConfig
    )


def test_config_version_changes_with_policy_or_catalog() -> None:
    base = config_version(policy(), catalog())
    assert base == config_version(policy(), catalog())
    data = policy().model_dump(mode="json")
    data["engine"]["backoff_ms"] = 201
    assert config_version(RoutingPolicy.model_validate(data), catalog()) != base
    cat = catalog().model_dump(mode="json")
    cat["models"][0]["input_price_micro_per_1m"] += 1
    assert config_version(policy(), Catalog.model_validate(cat)) != base


def test_every_model_round_trips_through_canonical_json() -> None:
    for model in (policy(), catalog()):
        again = type(model).model_validate_json(model.model_dump_json())
        assert again == model
