"""The request engine (PRD 3.7, 5.3, 6.3). The only module that advances the clock.

Order for one request: validation, team lookup, route, cache, attempt loop (breaker check, budget reserve,
provider call, settle, then retry, fall back or stop), cache store, trace. Every request gets one trace.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from switchyard.breaker import PROBE, CircuitBreaker, Health
from switchyard.budget import downgrade_chain
from switchyard.cache import CacheEntry, ExactCache, bypass_reason, cache_key
from switchyard.clock import Clock
from switchyard.config_io import config_version
from switchyard.ledger import Ledger
from switchyard.models import (
    DOWNGRADED,
    FALLBACK_USED,
    Attempt,
    BreakerState,
    CacheStatus,
    Catalog,
    DetailValue,
    Event,
    ModelSpec,
    Outcome,
    Overage,
    Request,
    RouteDecision,
    RouteRule,
    RoutingPolicy,
    Status,
    TeamConfig,
    Trace,
)
from switchyard.money import cost_micro
from switchyard.providers.base import Provider
from switchyard.routing import route, worst_case

# no_retry reasons, checked in this order (PRD 5.3)
MAX_ATTEMPTS = "max_attempts"
PER_REQUEST_RETRY_LIMIT = "per_request_retry_limit"
BREAKER_OPEN = "breaker_open"
RETRY_BUDGET_EXHAUSTED = "retry_budget_exhausted"


@dataclass
class _Run:
    request_id: str
    team: str
    app: str
    use_case: str
    task_class: str
    data_tags: tuple[str, ...]
    arrived_ms: int
    events: list[Event] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    route: RouteDecision | None = None
    cache: CacheStatus = CacheStatus.NOT_CHECKED
    cache_key: str = ""
    avoided_micro: int = 0


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


class Engine:
    def __init__(
        self,
        policy: RoutingPolicy,
        catalog: Catalog,
        teams: Mapping[str, TeamConfig],
        providers: Mapping[str, Provider],
        clock: Clock,
        ledger: Ledger,
        health: Health,
        cache: ExactCache,
    ) -> None:
        missing = sorted(set(catalog.providers()) - set(providers))
        if missing:
            raise ValueError(f"no provider adapter for: {', '.join(missing)}")
        self.policy = policy
        self.catalog = catalog
        self.teams = dict(teams)
        self.providers = dict(providers)
        self.clock = clock
        self.ledger = ledger
        self.health = health
        self.cache = cache
        self.config_version = config_version(policy, catalog)
        self.traces: list[Trace] = []
        self._seen: set[str] = set()

    # --- public --------------------------------------------------------------------------------------------

    def handle_raw(
        self, raw: Mapping[str, Any], *, arrival_ms: int | None = None, fake_output_tokens: int | None = None
    ) -> Trace:
        """Validate a raw request first. A malformed one ends ``bad_request`` with no attempts."""
        try:
            request = Request.model_validate(raw)
        except ValidationError:
            self._arrive(arrival_ms)
            run = _Run(
                request_id=_text(raw.get("request_id")),
                team=_text(raw.get("team")),
                app=_text(raw.get("app")),
                use_case=_text(raw.get("use_case")),
                task_class=_text(raw.get("task_class")),
                data_tags=(),
                arrived_ms=self.clock.now_ms(),
            )
            return self._finish(run, Status.BAD_REQUEST)
        return self.handle(request, arrival_ms=arrival_ms, fake_output_tokens=fake_output_tokens)

    def handle(
        self, request: Request, *, arrival_ms: int | None = None, fake_output_tokens: int | None = None
    ) -> Trace:
        self._arrive(arrival_ms)
        run = _Run(
            request_id=request.request_id,
            team=request.team,
            app=request.app,
            use_case=request.use_case,
            task_class=request.task_class,
            data_tags=request.data_tags,
            arrived_ms=self.clock.now_ms(),
        )
        # 0. validation
        unknown_tags = set(request.data_tags) - set(self.catalog.known_tags)
        duplicate = request.request_id in self._seen
        self._seen.add(request.request_id)
        if duplicate or unknown_tags:
            return self._finish(run, Status.BAD_REQUEST)
        # 1. team lookup
        team = self.teams.get(request.team)
        if team is None:
            return self._finish(run, Status.UNKNOWN_TEAM)
        # 2. route
        decision = route(request, self.policy, self.catalog)
        run.route = decision
        if not decision.chain:
            return self._finish(run, Status.NO_ROUTE)
        rule = next(r for r in self.policy.rules if r.id == decision.rule_id)
        # 3. cache
        key = cache_key(
            request, rule.id, decision.max_latency_ms, decision.max_cost_micro, self.config_version
        )
        bypass = bypass_reason(request, team.cache)
        if bypass is not None:
            run.cache = bypass
        else:
            run.cache_key = key
            hit = self.cache.get(request.team, key, self.clock.now_ms(), team.cache.ttl_ms)
            if hit is not None:
                run.cache = CacheStatus.HIT
                run.avoided_micro = hit.cost_micro
                return self._finish(run, Status.OK)
            run.cache = CacheStatus.MISS
        # 4. attempt loop
        status, served, text = self._attempt_loop(run, request, rule, team, decision, fake_output_tokens, key)
        # 5. cache store
        if (
            status is Status.OK
            and served is not None
            and run.cache is CacheStatus.MISS
            and DOWNGRADED not in run.flags
            and served.tier is rule.tiers[0]
        ):
            last = run.attempts[-1]
            self.cache.put(
                request.team,
                key,
                CacheEntry(
                    stored_at=last.finished_ms, model=served.id, cost_micro=last.cost_micro, text=text
                ),
                request,
                team.cache,
            )
        # 6. trace
        return self._finish(run, status)

    # --- internals -----------------------------------------------------------------------------------------

    def _arrive(self, arrival_ms: int | None) -> None:
        """A request starts at ``max(clock, arrival)``."""
        if arrival_ms is not None:
            now = self.clock.now_ms()
            if arrival_ms > now:
                self.clock.advance(arrival_ms - now)

    def _event(self, run: _Run, code: str, subject: str = "", **detail: DetailValue) -> None:
        run.events.append(Event(code=code, at_ms=self.clock.now_ms(), subject=subject, detail=detail))

    def _breaker_event(self, run: _Run, change: str | None, provider: str) -> None:
        if change is not None:
            self._event(run, change, provider)

    def _no_retry_reason(
        self, run: _Run, provider: str, retries_on: Mapping[str, int], breaker: CircuitBreaker, now: int
    ) -> str | None:
        settings = self.policy.engine
        if len(run.attempts) >= settings.max_attempts:
            return MAX_ATTEMPTS
        if retries_on.get(provider, 0) >= settings.max_retries_per_provider:
            return PER_REQUEST_RETRY_LIMIT
        if breaker.state is not BreakerState.CLOSED:
            return BREAKER_OPEN
        if not self.health.retry_budget(provider).can_retry(now):
            return RETRY_BUDGET_EXHAUSTED
        return None

    def _attempt_loop(
        self,
        run: _Run,
        request: Request,
        rule: RouteRule,
        team: TeamConfig,
        decision: RouteDecision,
        fake_output_tokens: int | None,
        key: str,
    ) -> tuple[Status, ModelSpec | None, str]:
        settings = self.policy.engine
        budget = team.budget
        chain = [self.catalog.model(m) for m in decision.chain]
        index = 0
        is_retry = False
        retries_on: dict[str, int] = {}
        first_seen: set[str] = set()
        touched: set[str] = set()  # models attempted or skipped by their breaker
        in_downgrade = False
        downgrade_attempted = False
        while index < len(chain):
            if len(run.attempts) >= settings.max_attempts:
                return Status.FAILED_EXHAUSTED, None, ""
            model = chain[index]
            provider = model.provider
            breaker = self.health.breaker(provider)
            now = self.clock.now_ms()
            allow = breaker.allow(now)
            if allow.change == PROBE:
                self._event(run, PROBE, provider)
            if not allow.allowed:
                self._event(run, "breaker_open_skip", model.id, provider=provider)
                touched.add(model.id)
                index += 1
                is_retry = False
                continue
            attempt_no = len(run.attempts) + 1
            reserved = self.ledger.reserve(
                request.team, request.request_id, attempt_no, worst_case(request, model), now, budget
            )
            if not reserved.accepted or reserved.entry is None:
                breaker.cancel(is_probe=allow.is_probe)
                self._event(
                    run,
                    "budget_refused",
                    model.id,
                    amount_micro=reserved.amount,
                    spent_micro=reserved.before.spent,
                    held_micro=reserved.before.held,
                    ceiling_micro=reserved.ceiling,
                )
                if in_downgrade:
                    index += 1
                    is_retry = False
                    continue
                if budget.overage is Overage.DOWNGRADE:
                    in_downgrade = True
                    attempted = {a.model for a in run.attempts}
                    lower = downgrade_chain(request, rule, self.catalog, model.tier, attempted)
                    self._event(run, "downgrade_chain", rule.id, models=[m.id for m in lower])
                    chain = chain[: index + 1] + lower
                    index += 1
                    is_retry = False
                    continue
                return Status.DENIED_BUDGET, None, ""
            run.flags.update(reserved.flags)
            if in_downgrade:
                downgrade_attempted = True
            retry_budget = self.health.retry_budget(provider)
            if provider not in first_seen:
                first_seen.add(provider)
                retry_budget.note_first_attempt(now)
            elif is_retry:
                retry_budget.note_retry(now)
                retries_on[provider] = retries_on.get(provider, 0) + 1
            try:
                result = self.providers[provider].call(
                    model,
                    request,
                    now,
                    decision.max_latency_ms,
                    fake_output_tokens,
                    key.removeprefix("sha256:")[:12],
                )
            except Exception:
                # A broken adapter (e.g. an invalid fake script) is a programming error, not an outcome.
                # Close the hold and give the breaker permit back so nothing leaks, then re-raise.
                self.ledger.release(
                    request.request_id, attempt_no, now, memo="provider adapter raised; no call was made"
                )
                breaker.cancel(is_probe=allow.is_probe)
                raise
            self.clock.advance(result.latency_ms)
            finished = self.clock.now_ms()
            cost = cost_micro(
                result.input_tokens,
                result.output_tokens,
                model.input_price_micro_per_1m,
                model.output_price_micro_per_1m,
            )
            settle = self.ledger.settle(request.request_id, attempt_no, cost, finished)
            run.attempts.append(
                Attempt(
                    attempt=attempt_no,
                    provider=provider,
                    model=model.id,
                    started_ms=now,
                    finished_ms=finished,
                    outcome=result.outcome,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    reserved_micro=reserved.amount,
                    cost_micro=cost,
                    period=reserved.entry.period,
                    reserve_seq=reserved.entry.seq,
                    settle_seq=settle.seq,
                )
            )
            if result.outcome is Outcome.OK:
                self._breaker_event(
                    run, breaker.record(Outcome.OK, finished, is_probe=allow.is_probe), provider
                )
                if any(m != model.id for m in touched):
                    run.flags.add(FALLBACK_USED)
                if in_downgrade:
                    run.flags.add(DOWNGRADED)
                return Status.OK, model, result.text
            touched.add(model.id)
            if result.outcome is Outcome.BAD_REQUEST:
                breaker.cancel(is_probe=allow.is_probe)
                return Status.BAD_REQUEST, None, ""
            self._breaker_event(
                run, breaker.record(result.outcome, finished, is_probe=allow.is_probe), provider
            )
            reason = self._no_retry_reason(run, provider, retries_on, breaker, finished)
            if reason is None:
                self._event(run, "retry", model.id)
                self.clock.advance(settings.backoff_ms)
                is_retry = True
                continue
            self._event(run, "no_retry", model.id, reason=reason)
            if reason == MAX_ATTEMPTS:
                return Status.FAILED_EXHAUSTED, None, ""
            index += 1
            is_retry = False
        if in_downgrade and not downgrade_attempted:
            return Status.DENIED_BUDGET, None, ""
        return Status.FAILED_EXHAUSTED, None, ""

    def _finish(self, run: _Run, status: Status) -> Trace:
        trace = Trace(
            request_id=run.request_id,
            team=run.team,
            app=run.app,
            use_case=run.use_case,
            task_class=run.task_class,
            data_tags=run.data_tags,
            arrived_ms=run.arrived_ms,
            finished_ms=self.clock.now_ms(),
            config_version=self.config_version,
            route=run.route,
            cache_key=run.cache_key,
            cache=run.cache,
            avoided_micro=run.avoided_micro,
            events=tuple(run.events),
            attempts=tuple(run.attempts),
            status=status,
            flags=tuple(sorted(run.flags)),
            cost_micro=sum(a.cost_micro for a in run.attempts),
        )
        self.traces.append(trace)
        return trace
