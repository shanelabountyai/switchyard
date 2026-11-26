"""Shared builders for tests. Everything here is illustrative and uses only fake providers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any

from switchyard.breaker import Health
from switchyard.cache import ExactCache
from switchyard.cases import CaseResult, load_case, run_case
from switchyard.clock import FakeClock, parse_utc
from switchyard.config_io import load_catalog, load_policy
from switchyard.engine import Engine
from switchyard.ledger import Ledger
from switchyard.models import Catalog, Request, RoutingPolicy, TeamConfig
from switchyard.providers.fake import FakeProvider, ScriptWindow

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
CASES = sorted((FIXTURES / "cases").glob("*.yaml"))
START_MS = parse_utc("2026-11-02T09:00:00.000Z")
BIG = 1_000_000_000_000


@cache
def catalog() -> Catalog:
    return load_catalog(FIXTURES / "catalog" / "illustrative-catalog.yaml")


@cache
def policy() -> RoutingPolicy:
    return load_policy(FIXTURES / "policy" / "illustrative-policy.yaml")


@cache
def case_result(path: Path) -> CaseResult:
    return run_case(load_case(path))


def team(
    soft: int = BIG,
    hard: int = BIG,
    overage: str = "deny",
    cap: int = 0,
    cache_on: bool = False,
    ttl_ms: int = 60_000,
) -> TeamConfig:
    return TeamConfig.model_validate(
        {
            "budget": {
                "period": "calendar_month_utc",
                "soft_limit_micro": soft,
                "hard_limit_micro": hard,
                "overage": overage,
                "overage_cap_micro": cap,
            },
            "cache": {"enabled": cache_on, "ttl_ms": ttl_ms},
        }
    )


def request(
    request_id: str = "r1",
    team_name: str = "support",
    app: str = "helpdesk",
    use_case: str = "triage",
    task_class: str = "classify",
    input_tokens: int = 400,
    max_output_tokens: int = 50,
    content: str = "Illustrative text",
    **extra: Any,
) -> Request:
    data: dict[str, Any] = {
        "request_id": request_id,
        "team": team_name,
        "app": app,
        "use_case": use_case,
        "task_class": task_class,
        "messages": [{"role": "user", "content": content}],
        "input_tokens": input_tokens,
        "max_output_tokens": max_output_tokens,
    }
    data.update(extra)
    return Request.model_validate(data)


def engine(
    teams: Mapping[str, TeamConfig] | None = None,
    scripts: Mapping[str, Sequence[ScriptWindow]] | None = None,
    the_policy: RoutingPolicy | None = None,
    the_catalog: Catalog | None = None,
    start_ms: int = START_MS,
) -> Engine:
    pol = the_policy or policy()
    cat = the_catalog or catalog()
    scripts = scripts or {}
    providers = {p: FakeProvider(p, scripts.get(p, ()), start_ms) for p in cat.providers()}
    return Engine(
        pol,
        cat,
        teams if teams is not None else {"support": team(), "marketing": team(), "research": team()},
        providers,
        FakeClock(start_ms),
        Ledger(),
        Health(pol.engine.breaker, pol.engine.retry_budget),
        ExactCache(pol.engine.cache_max_entries),
    )


def window(from_ms: int, to_ms: int, outcome: str, **extra: int) -> ScriptWindow:
    return ScriptWindow.model_validate({"from_ms": from_ms, "to_ms": to_ms, "outcome": outcome, **extra})
