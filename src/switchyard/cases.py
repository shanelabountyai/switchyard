"""Golden case files: load, run, and compare against their ``expect`` blocks (fixtures/README.md).

Used by the golden tests and the CLI. Does file I/O when loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from switchyard.breaker import Health
from switchyard.cache import ExactCache
from switchyard.clock import FakeClock, parse_utc, period_of
from switchyard.config_io import (
    ConfigError,
    load_catalog,
    load_policy,
    load_teams,
    load_yaml,
    validation_message,
)
from switchyard.engine import Engine
from switchyard.ledger import Ledger, LedgerError
from switchyard.models import (
    BreakerState,
    CacheStatus,
    Catalog,
    Frozen,
    LedgerEntry,
    Name,
    NonNegInt,
    PosInt,
    ReportRow,
    RoutingPolicy,
    Status,
    TeamConfig,
    Trace,
)
from switchyard.money import Micro, SignedMicro, cost_micro
from switchyard.providers.fake import FakeProvider, ScriptError, ScriptWindow
from switchyard.report import daily_rows
from switchyard.trace import Difference, reconcile

END = "end"


class CaseError(ValueError):
    """A case file is invalid or cannot be run as written."""


# --- file format -------------------------------------------------------------------------------------------


class OpeningBalance(Frozen):
    team: Name
    amount_micro: SignedMicro
    memo: Name


class AttemptExpect(Frozen):
    model: Name
    started_ms: NonNegInt | None = None
    finished_ms: NonNegInt | None = None
    outcome: str | None = None
    input_tokens: NonNegInt | None = None
    output_tokens: NonNegInt | None = None
    reserved_micro: Micro | None = None
    cost_micro: Micro | None = None


class BalanceExpect(Frozen):
    spent_micro: SignedMicro
    held_micro: Micro


class RequestExpect(Frozen):
    status: Status
    rule: str | None = None
    chain: tuple[str, ...] | None = None
    cache: CacheStatus | None = None
    attempts: tuple[AttemptExpect, ...] | None = None
    events: tuple[str, ...] = ()
    event_times: tuple[NonNegInt, ...] | None = None  # at_ms of each event, relative to start_at
    flags: tuple[str, ...] = ()
    cost_micro: Micro | None = None
    avoided_micro: Micro | None = None
    route_reasons: tuple[str, ...] | None = None
    breakers_after: dict[str, BreakerState] | None = None
    balance_after: BalanceExpect | None = None


class CaseRequest(Frozen):
    we: Name
    at_ms: NonNegInt
    fake_output_tokens: NonNegInt | None = None
    request: dict[str, Any]
    expect: RequestExpect


class TeamBalanceExpect(Frozen):
    team: Name
    period: Name
    spent_micro: SignedMicro
    held_micro: Micro


class ExpectEnd(Frozen):
    balances: tuple[TeamBalanceExpect, ...] = ()
    breakers: dict[str, BreakerState] | None = None
    ledger_entries: NonNegInt | None = None
    cache_entries: dict[str, NonNegInt] | None = None
    reconcile: Literal["clean"] | None = None
    report: tuple[dict[str, int | str], ...] | None = None
    report_rows: NonNegInt | None = None


class StepExpect(Frozen):
    accepted: bool | None = None
    amount_micro: Micro | None = None
    flags: tuple[str, ...] = ()
    spent_micro: SignedMicro | None = None
    held_micro: Micro | None = None


class Step(Frozen):
    op: Literal["reserve", "settle"]
    at_ms: NonNegInt
    team: Name
    request_id: Name
    attempt: PosInt
    model: Name
    input_tokens: NonNegInt
    max_output_tokens: PosInt | None = None
    output_tokens: NonNegInt | None = None
    expect: StepExpect

    @model_validator(mode="after")
    def _check(self) -> Step:
        if self.op == "reserve" and self.max_output_tokens is None:
            raise ValueError("a reserve step needs max_output_tokens")
        if self.op == "settle" and self.output_tokens is None:
            raise ValueError("a settle step needs output_tokens")
        return self


class _CaseBase(Frozen):
    id: Name
    illustrative: Literal[True]
    description: str = ""
    start_at: Name
    catalog: Name
    teams: dict[str, Any]
    covers: tuple[Name, ...] = Field(min_length=1)
    opening: tuple[OpeningBalance, ...] = ()


class SequentialCase(_CaseBase):
    kind: Literal["sequential"]
    policy: Name
    scripts: dict[str, tuple[ScriptWindow, ...]] = Field(default_factory=dict)
    requests: tuple[CaseRequest, ...] = Field(min_length=1)
    expect_end: ExpectEnd

    @model_validator(mode="after")
    def _check(self) -> SequentialCase:
        if self.expect_end.reconcile != "clean":
            raise ValueError("expect_end.reconcile: clean is required in every sequential case")
        return self


class InterleavedCase(_CaseBase):
    kind: Literal["interleaved"]
    steps: tuple[Step, ...] = Field(min_length=1)
    expect_end: ExpectEnd


Case = Annotated[SequentialCase | InterleavedCase, Field(discriminator="kind")]
_CASE_ADAPTER: TypeAdapter[SequentialCase | InterleavedCase] = TypeAdapter(Case)


@dataclass(frozen=True)
class LoadedCase:
    path: Path
    case: SequentialCase | InterleavedCase
    catalog: Catalog
    policy: RoutingPolicy | None
    teams: dict[str, TeamConfig]
    start_ms: int


def load_case(path: Path) -> LoadedCase:
    data = load_yaml(path)
    try:
        case = _CASE_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise ConfigError(validation_message(str(path), exc)) from None
    base = path.parent
    catalog = load_catalog(base.joinpath(case.catalog).resolve())
    policy = load_policy(base.joinpath(case.policy).resolve()) if isinstance(case, SequentialCase) else None
    teams = load_teams(case.teams, f"{path}: teams")
    try:
        start_ms = parse_utc(case.start_at)
    except ValueError as exc:
        raise ConfigError(f"{path}: start_at: {exc}") from None
    if isinstance(case, SequentialCase):
        unknown = sorted(set(case.scripts) - set(catalog.providers()))
        if unknown:
            raise ConfigError(f"{path}: scripts: unknown provider {', '.join(unknown)}")
    return LoadedCase(path, case, catalog, policy, teams, start_ms)


# --- running -----------------------------------------------------------------------------------------------


@dataclass
class CaseResult:
    loaded: LoadedCase
    ledger: Ledger
    traces: list[Trace] = field(default_factory=list)
    breakers: dict[str, BreakerState] = field(default_factory=dict)
    cache_sizes: dict[str, int] = field(default_factory=dict)
    differences: list[Difference] = field(default_factory=list)
    report_rows: list[ReportRow] | None = None
    # (label, message): label is the request's ``we`` or ``end``
    mismatches: list[tuple[str, str]] = field(default_factory=list)

    def mismatches_for(self, we: str) -> list[str]:
        """Mismatches of the requests labelled ``we``, plus every end-of-case mismatch."""
        return [msg for label, msg in self.mismatches if label in (we, END)]


def _diff(
    out: list[tuple[str, str]], label: str, where: str, name: str, expected: object, actual: object
) -> None:
    if expected != actual:
        out.append((label, f"{where}: {name}: expected {expected!r}, got {actual!r}"))


def _open_balances(ledger: Ledger, loaded: LoadedCase) -> None:
    for opening in loaded.case.opening:
        ledger.adjust(opening.team, opening.amount_micro, loaded.start_ms, opening.memo)


def run_case(loaded: LoadedCase) -> CaseResult:
    if isinstance(loaded.case, InterleavedCase):
        return _run_interleaved(loaded, loaded.case)
    return _run_sequential(loaded, loaded.case)


def _check_request(
    result: CaseResult, label: str, item: CaseRequest, trace: Trace, engine: Engine, start_ms: int
) -> None:
    out = result.mismatches
    exp = item.expect
    where = f"{label} {trace.request_id}"
    _diff(out, label, where, "status", exp.status, trace.status)
    if exp.rule is not None:
        _diff(out, label, where, "rule", exp.rule, trace.route.rule_id if trace.route else None)
    if exp.chain is not None:
        _diff(out, label, where, "chain", list(exp.chain), list(trace.route.chain) if trace.route else [])
    if exp.cache is not None:
        _diff(out, label, where, "cache", exp.cache, trace.cache)
    if exp.route_reasons is not None:
        actual = [f"{r.code}:{r.subject}" for r in trace.route.reasons] if trace.route else []
        _diff(out, label, where, "route_reasons", list(exp.route_reasons), actual)
    _diff(out, label, where, "events", list(exp.events), [e.label() for e in trace.events])
    if exp.event_times is not None:
        times = [e.at_ms - start_ms for e in trace.events]
        _diff(out, label, where, "event_times", list(exp.event_times), times)
    _diff(out, label, where, "flags", sorted(exp.flags), list(trace.flags))
    if exp.cost_micro is not None:
        _diff(out, label, where, "cost_micro", exp.cost_micro, trace.cost_micro)
    if exp.avoided_micro is not None:
        _diff(out, label, where, "avoided_micro", exp.avoided_micro, trace.avoided_micro)
    if exp.attempts is not None:
        _diff(out, label, where, "attempt count", len(exp.attempts), len(trace.attempts))
        for i, (want, got) in enumerate(zip(exp.attempts, trace.attempts, strict=False), start=1):
            actual_values = {
                "model": got.model,
                "started_ms": got.started_ms - start_ms,
                "finished_ms": got.finished_ms - start_ms,
                "outcome": got.outcome.value,
                "input_tokens": got.input_tokens,
                "output_tokens": got.output_tokens,
                "reserved_micro": got.reserved_micro,
                "cost_micro": got.cost_micro,
            }
            for name, expected in want.model_dump(exclude_none=True).items():
                _diff(out, label, f"{where} attempt {i}", name, expected, actual_values[name])
    if exp.breakers_after is not None:
        states = engine.health.states()
        for provider, state in sorted(exp.breakers_after.items()):
            _diff(out, label, where, f"breaker {provider}", state, states.get(provider, BreakerState.CLOSED))
    if exp.balance_after is not None:
        balance = engine.ledger.balance(trace.team, period_of(trace.arrived_ms))
        _diff(out, label, where, "spent_micro", exp.balance_after.spent_micro, balance.spent)
        _diff(out, label, where, "held_micro", exp.balance_after.held_micro, balance.held)


def _run_sequential(loaded: LoadedCase, case: SequentialCase) -> CaseResult:
    assert loaded.policy is not None
    clock = FakeClock(loaded.start_ms)
    ledger = Ledger()
    _open_balances(ledger, loaded)
    health = Health(loaded.policy.engine.breaker, loaded.policy.engine.retry_budget)
    cache = ExactCache(loaded.policy.engine.cache_max_entries)
    providers = {
        p: FakeProvider(p, case.scripts.get(p, ()), loaded.start_ms) for p in loaded.catalog.providers()
    }
    engine = Engine(loaded.policy, loaded.catalog, loaded.teams, providers, clock, ledger, health, cache)
    result = CaseResult(loaded=loaded, ledger=ledger)
    for item in case.requests:
        arrival = loaded.start_ms + item.at_ms
        if arrival < clock.now_ms():
            raise CaseError(
                f"{loaded.path}: {item.we}: at_ms {item.at_ms} is earlier than the previous request's finish "
                f"({clock.now_ms() - loaded.start_ms})"
            )
        try:
            trace = engine.handle_raw(
                item.request, arrival_ms=arrival, fake_output_tokens=item.fake_output_tokens
            )
        except ScriptError as exc:
            raise CaseError(f"{loaded.path}: {item.we}: {exc}") from None
        _check_request(result, item.we, item, trace, engine, loaded.start_ms)
    result.traces = list(engine.traces)
    result.breakers = health.states()
    result.cache_sizes = cache.sizes()
    result.differences = reconcile(result.traces, ledger.entries())
    if not result.differences:
        result.report_rows = daily_rows(result.traces, ledger.entries(), loaded.teams)
    _check_end(result, case.expect_end)
    return result


def _run_interleaved(loaded: LoadedCase, case: InterleavedCase) -> CaseResult:
    ledger = Ledger()
    _open_balances(ledger, loaded)
    result = CaseResult(loaded=loaded, ledger=ledger)
    label = case.covers[0]
    for number, step in enumerate(case.steps, start=1):
        where = f"{label} step {number} ({step.op} {step.request_id})"
        team = loaded.teams.get(step.team)
        if team is None:
            raise CaseError(f"{loaded.path}: step {number}: team {step.team!r} is not configured")
        model = loaded.catalog.model(step.model)
        now = loaded.start_ms + step.at_ms
        out = result.mismatches
        try:
            if step.op == "reserve":
                assert step.max_output_tokens is not None
                amount = cost_micro(
                    step.input_tokens,
                    step.max_output_tokens,
                    model.input_price_micro_per_1m,
                    model.output_price_micro_per_1m,
                )
                reserved = ledger.reserve(step.team, step.request_id, step.attempt, amount, now, team.budget)
                if step.expect.accepted is not None:
                    _diff(out, label, where, "accepted", step.expect.accepted, reserved.accepted)
                _diff(out, label, where, "flags", sorted(step.expect.flags), list(reserved.flags))
            else:
                assert step.output_tokens is not None
                amount = cost_micro(
                    step.input_tokens,
                    step.output_tokens,
                    model.input_price_micro_per_1m,
                    model.output_price_micro_per_1m,
                )
                ledger.settle(step.request_id, step.attempt, amount, now)
        except LedgerError as exc:
            out.append((label, f"{where}: ledger error: {exc}"))
            continue
        if step.expect.amount_micro is not None:
            _diff(out, label, where, "amount_micro", step.expect.amount_micro, amount)
        balance = ledger.balance(step.team, period_of(now))
        if step.expect.spent_micro is not None:
            _diff(out, label, where, "spent_micro", step.expect.spent_micro, balance.spent)
        if step.expect.held_micro is not None:
            _diff(out, label, where, "held_micro", step.expect.held_micro, balance.held)
    _check_end(result, case.expect_end)
    return result


def _check_end(result: CaseResult, end: ExpectEnd) -> None:
    out = result.mismatches
    ledger = result.ledger
    for want in end.balances:
        balance = ledger.balance(want.team, want.period)
        where = f"end {want.team} {want.period}"
        _diff(out, END, where, "spent_micro", want.spent_micro, balance.spent)
        _diff(out, END, where, "held_micro", want.held_micro, balance.held)
    if end.breakers is not None:
        for provider, state in sorted(end.breakers.items()):
            _diff(
                out,
                END,
                "end",
                f"breaker {provider}",
                state,
                result.breakers.get(provider, BreakerState.CLOSED),
            )
    if end.ledger_entries is not None:
        _diff(out, END, "end", "ledger_entries", end.ledger_entries, len(ledger.entries()))
    if end.cache_entries is not None:
        for team, size in sorted(end.cache_entries.items()):
            _diff(out, END, "end", f"cache_entries {team}", size, result.cache_sizes.get(team, 0))
    if end.reconcile == "clean":
        _diff(out, END, "end", "reconcile", [], [str(d) for d in result.differences])
    if end.report is not None or end.report_rows is not None:
        if result.report_rows is None:
            out.append((END, "end: report: not produced (reconcile found differences)"))
            return
        if end.report_rows is not None:
            _diff(out, END, "end", "report row count", end.report_rows, len(result.report_rows))
        for want_row in end.report or ():
            key = (want_row.get("date"), want_row.get("team"))
            match = [r for r in result.report_rows if (r.date, r.team) == key]
            if not match:
                out.append((END, f"end: report: no row for {key}"))
                continue
            got = match[0].model_dump(mode="json")
            for name, expected in sorted(want_row.items()):
                _diff(out, END, f"end report {key}", name, expected, got.get(name))


def ledger_entries(result: CaseResult) -> tuple[LedgerEntry, ...]:
    return result.ledger.entries()
