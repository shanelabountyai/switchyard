"""Frozen pydantic models for config, requests, the ledger and traces.

Every integer field is strict: a float, a bool or a numeric string is rejected at the boundary (AC-A6, AC-C7).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator, model_validator

from switchyard.money import Micro, SignedMicro

NonNegInt = Annotated[int, Strict(), Field(ge=0)]
PosInt = Annotated[int, Strict(), Field(ge=1)]
Pct = Annotated[int, Strict(), Field(ge=0, le=100)]
Name = Annotated[str, Strict(), Field(min_length=1)]
DetailValue = int | str | list[str]

WILDCARD = "*"
_PERIOD_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
_DECIMAL_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- enums -------------------------------------------------------------------------------------------------


class Tier(StrEnum):
    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]


_TIER_RANK = {Tier.ECONOMY: 0, Tier.STANDARD: 1, Tier.PREMIUM: 2}
TIERS_LOW_TO_HIGH = (Tier.ECONOMY, Tier.STANDARD, Tier.PREMIUM)


class Outcome(StrEnum):
    OK = "ok"
    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    RATE_LIMITED = "rate_limited"
    BAD_REQUEST = "bad_request"

    @property
    def is_failure(self) -> bool:
        """``timeout``, ``server_error`` and ``rate_limited`` count against health (PRD 5.1)."""
        return self in (Outcome.TIMEOUT, Outcome.SERVER_ERROR, Outcome.RATE_LIMITED)


class Status(StrEnum):
    OK = "ok"
    NO_ROUTE = "no_route"
    DENIED_BUDGET = "denied_budget"
    FAILED_EXHAUSTED = "failed_exhausted"
    BAD_REQUEST = "bad_request"
    UNKNOWN_TEAM = "unknown_team"


class EntryKind(StrEnum):
    RESERVE = "reserve"
    SETTLE = "settle"
    RELEASE = "release"
    ADJUSTMENT = "adjustment"


class Overage(StrEnum):
    DENY = "deny"
    DOWNGRADE = "downgrade"
    ALLOW_WITH_FLAG = "allow_with_flag"


class CacheStatus(StrEnum):
    HIT = "hit"
    MISS = "miss"
    BYPASS_SENSITIVE = "bypass_sensitive"
    BYPASS_TEAM_OPT_OUT = "bypass_team_opt_out"
    BYPASS_NONDETERMINISTIC = "bypass_nondeterministic"
    NOT_CHECKED = "not_checked"


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# Flag names (PRD 6.1, 8.1).
SOFT_LIMIT_CROSSED = "soft_limit_crossed"
OVER_HARD_LIMIT = "over_hard_limit"
DOWNGRADED = "downgraded"
FALLBACK_USED = "fallback_used"
UNSETTLED = "unsettled"
TRACE_FLAGS = (DOWNGRADED, FALLBACK_USED, OVER_HARD_LIMIT, SOFT_LIMIT_CROSSED)


# --- catalog -----------------------------------------------------------------------------------------------


class ModelSpec(Frozen):
    id: Name
    provider: Name
    tier: Tier
    input_price_micro_per_1m: Micro
    output_price_micro_per_1m: Micro
    declared_latency_ms: NonNegInt
    cleared_tags: tuple[Name, ...] = ()


class Catalog(Frozen):
    illustrative: Literal[True]
    known_tags: tuple[Name, ...]
    models: tuple[ModelSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Catalog:
        ids = [m.id for m in self.models]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate model id: {', '.join(dupes)}")
        if len(set(self.known_tags)) != len(self.known_tags):
            raise ValueError("duplicate tag in known_tags")
        for model in self.models:
            unknown = sorted(set(model.cleared_tags) - set(self.known_tags))
            if unknown:
                raise ValueError(f"model {model.id}: unknown tag {', '.join(unknown)}")
        return self

    def model(self, model_id: str) -> ModelSpec:
        for spec in self.models:
            if spec.id == model_id:
                return spec
        raise KeyError(model_id)

    def providers(self) -> list[str]:
        return sorted({m.provider for m in self.models})


# --- policy ------------------------------------------------------------------------------------------------


class RouteRule(Frozen):
    id: Name
    team: Name
    app: Name
    use_case: Name
    task_class: Name
    tiers: tuple[Tier, ...] = Field(min_length=1)
    max_latency_ms: NonNegInt
    max_cost_micro: Micro
    min_tier: Tier

    @model_validator(mode="after")
    def _check(self) -> RouteRule:
        if len(set(self.tiers)) != len(self.tiers):
            raise ValueError(f"rule {self.id}: a tier is listed twice")
        if self.min_tier.rank > self.tiers[0].rank:
            raise ValueError(
                f"rule {self.id}: min_tier {self.min_tier} is above the first tier {self.tiers[0]}"
            )
        return self

    @property
    def is_catch_all(self) -> bool:
        return all(m == WILDCARD for m in (self.team, self.app, self.use_case, self.task_class))


class BreakerSettings(Frozen):
    window_ms: PosInt
    min_calls: PosInt
    failure_rate_pct: Pct
    open_ms: PosInt


class RetryBudgetSettings(Frozen):
    window_ms: PosInt
    ratio_pct: Pct
    min_retries: NonNegInt


class EngineSettings(Frozen):
    max_attempts: PosInt
    max_retries_per_provider: NonNegInt
    backoff_ms: NonNegInt
    breaker: BreakerSettings
    retry_budget: RetryBudgetSettings
    reservation_ttl_ms: PosInt
    cache_max_entries: PosInt


class RoutingPolicy(Frozen):
    illustrative: Literal[True]
    rules: tuple[RouteRule, ...] = Field(min_length=1)
    engine: EngineSettings

    @model_validator(mode="after")
    def _check(self) -> RoutingPolicy:
        ids = [r.id for r in self.rules]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate rule id: {', '.join(dupes)}")
        if not self.rules[-1].is_catch_all:
            raise ValueError("the last rule must be a catch-all (team, app, use_case and task_class all '*')")
        return self


# --- teams -------------------------------------------------------------------------------------------------


class TeamBudget(Frozen):
    period: Literal["calendar_month_utc"]
    soft_limit_micro: Micro
    hard_limit_micro: Micro
    overage: Overage
    overage_cap_micro: Micro

    @model_validator(mode="after")
    def _check(self) -> TeamBudget:
        if self.soft_limit_micro > self.hard_limit_micro:
            raise ValueError("soft_limit_micro must be <= hard_limit_micro")
        if self.overage is Overage.ALLOW_WITH_FLAG and self.overage_cap_micro <= 0:
            raise ValueError("allow_with_flag requires overage_cap_micro > 0")
        if self.overage is not Overage.ALLOW_WITH_FLAG and self.overage_cap_micro != 0:
            raise ValueError("overage_cap_micro must be 0 unless overage is allow_with_flag")
        return self

    @property
    def ceiling(self) -> int:
        cap = self.overage_cap_micro if self.overage is Overage.ALLOW_WITH_FLAG else 0
        return self.hard_limit_micro + cap


class TeamCache(Frozen):
    enabled: Annotated[bool, Strict()]
    ttl_ms: PosInt


class TeamConfig(Frozen):
    budget: TeamBudget
    cache: TeamCache


# --- request -----------------------------------------------------------------------------------------------


class Message(Frozen):
    role: Name
    content: Annotated[str, Strict()]


class Request(Frozen):
    request_id: Name
    team: Name
    app: Name
    use_case: Name
    task_class: Name
    data_tags: tuple[Name, ...] = ()
    messages: tuple[Message, ...] = Field(min_length=1)
    input_tokens: NonNegInt
    max_output_tokens: PosInt
    temperature: Annotated[str, Strict()] = "0"
    max_latency_ms: NonNegInt | None = None
    max_cost_micro: Micro | None = None

    @field_validator("data_tags")
    @classmethod
    def _sorted_unique(cls, tags: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(tags)) != len(tags):
            raise ValueError("a data tag is listed twice")
        return tuple(sorted(tags))

    @field_validator("temperature")
    @classmethod
    def _decimal(cls, value: str) -> str:
        if not _DECIMAL_RE.match(value):
            raise ValueError(f"temperature must be a decimal string, got {value!r}")
        return value


# --- routing decision --------------------------------------------------------------------------------------


class Reason(Frozen):
    code: Name
    subject: Name
    detail: dict[str, DetailValue] = Field(default_factory=dict)


class RouteDecision(Frozen):
    rule_id: Name
    max_latency_ms: NonNegInt
    max_cost_micro: Micro
    chain: tuple[str, ...]
    reasons: tuple[Reason, ...]


# --- ledger ------------------------------------------------------------------------------------------------


class LedgerEntry(Frozen):
    seq: PosInt
    ts_ms: NonNegInt
    team: Name
    period: Name
    kind: EntryKind
    request_id: Name | None = None
    attempt: PosInt | None = None
    amount_micro: SignedMicro
    ref_seq: PosInt | None = None
    flags: tuple[Name, ...] = ()
    memo: Annotated[str, Strict()] = ""

    @model_validator(mode="after")
    def _check(self) -> LedgerEntry:
        if not _PERIOD_RE.match(self.period):
            raise ValueError(f"period must be YYYY-MM, got {self.period!r}")
        is_adjustment = self.kind is EntryKind.ADJUSTMENT
        if is_adjustment != (self.request_id is None) or is_adjustment != (self.attempt is None):
            raise ValueError("request_id and attempt are set on every entry except an adjustment")
        if (self.kind in (EntryKind.SETTLE, EntryKind.RELEASE)) != (self.ref_seq is not None):
            raise ValueError("ref_seq is set exactly on settle and release")
        if self.kind in (EntryKind.RESERVE, EntryKind.SETTLE) and self.amount_micro < 0:
            raise ValueError(f"{self.kind} amount must be >= 0")
        if self.kind is EntryKind.RELEASE and self.amount_micro != 0:
            raise ValueError("release amount must be 0")
        if self.kind in (EntryKind.RELEASE, EntryKind.ADJUSTMENT) and not self.memo:
            raise ValueError(f"{self.kind} requires a memo")
        return self


# --- trace -------------------------------------------------------------------------------------------------


class Attempt(Frozen):
    attempt: PosInt
    provider: Name
    model: Name
    started_ms: NonNegInt
    finished_ms: NonNegInt
    outcome: Outcome
    input_tokens: NonNegInt
    output_tokens: NonNegInt
    reserved_micro: Micro
    cost_micro: Micro
    period: Name
    reserve_seq: PosInt
    settle_seq: PosInt


class Event(Frozen):
    code: Name
    at_ms: NonNegInt
    subject: Annotated[str, Strict()] = ""
    detail: dict[str, DetailValue] = Field(default_factory=dict)

    def label(self) -> str:
        """``no_retry:<reason>`` and ``budget_refused:<amount>``; other codes as is."""
        if self.code == "no_retry":
            return f"no_retry:{self.detail['reason']}"
        if self.code == "budget_refused":
            return f"budget_refused:{self.detail['amount_micro']}"
        return self.code


class Trace(Frozen):
    request_id: Annotated[str, Strict()]
    team: Annotated[str, Strict()]
    app: Annotated[str, Strict()]
    use_case: Annotated[str, Strict()]
    task_class: Annotated[str, Strict()]
    data_tags: tuple[str, ...]
    arrived_ms: NonNegInt
    finished_ms: NonNegInt
    config_version: Name
    route: RouteDecision | None
    cache_key: Annotated[str, Strict()]
    cache: CacheStatus
    avoided_micro: Micro
    events: tuple[Event, ...]
    attempts: tuple[Attempt, ...]
    status: Status
    flags: tuple[str, ...]
    cost_micro: Micro

    @model_validator(mode="after")
    def _check(self) -> Trace:
        if self.cost_micro != sum(a.cost_micro for a in self.attempts):
            raise ValueError("trace cost_micro must equal the sum of its attempts' cost_micro")
        return self


# --- report ------------------------------------------------------------------------------------------------


class ReportRow(Frozen):
    date: Name
    team: Annotated[str, Strict()]  # "" for a malformed request with no team
    requests: NonNegInt
    ok: NonNegInt
    no_route: NonNegInt
    denied_budget: NonNegInt
    failed_exhausted: NonNegInt
    bad_request: NonNegInt
    unknown_team: NonNegInt
    cache_hits: NonNegInt
    attempts: NonNegInt
    failed_attempts: NonNegInt
    input_tokens: NonNegInt
    output_tokens: NonNegInt
    cost_micro: Micro
    cost_usd: Name
    avoided_micro: Micro
    adjustments_micro: SignedMicro
    soft_limit_crossed: NonNegInt
    over_hard_limit: NonNegInt
    downgraded: NonNegInt
    fallback_used: NonNegInt
    period: Name
    spent_micro: SignedMicro
    soft_limit_micro: Micro | None
    hard_limit_micro: Micro | None
    remaining_micro: SignedMicro | None
