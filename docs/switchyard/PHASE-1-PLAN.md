# Switchyard: Phase 1 plan (pure engines)

Scope: PRD sections 3 to 8 and 10 to 11 as pure, deterministic Python. **No HTTP server, no network, no API key,
no live model call.** Section 9 (Yardstick hand-off) is design only and is not built. Branch `phase-1` cut from
`prd` (D-023), one draft pull request, never merged by Claude.

Done means: CI green, SW-01 to SW-09 green, all 30 worked examples reproduced by golden tests.

## 1. Repository layout after Phase 1

```text
pyproject.toml            uv.lock            .gitignore
.github/workflows/ci.yml
src/switchyard/
  __init__.py   money.py   clock.py   canonical.py   models.py   config_io.py
  routing.py    breaker.py ledger.py  budget.py      cache.py    engine.py
  trace.py      report.py  cases.py   cli.py
  providers/__init__.py    providers/base.py    providers/fake.py
tests/
  invariants/test_sw01_determinism.py … test_sw09_fakes_only.py
  golden/test_worked_examples.py
  test_money.py  test_routing.py  test_breaker.py  test_ledger.py  test_budget.py
  test_cache.py  test_fake_provider.py  test_engine.py  test_trace.py  test_report.py
  test_config_io.py  test_cli.py
fixtures/
  README.md
  catalog/illustrative-catalog.yaml
  policy/illustrative-policy.yaml
  cases/c01-routing-basics.yaml … c15-cache-opt-out.yaml
examples/
  provider-outage-report.md        (written by the CLI from c02, checked in CI for drift)
```

## 2. `pyproject.toml`

Versions are copied from Yardstick's `pyproject.toml` at commit `710c604`. Only the packages Switchyard uses are
listed (D-019). There is no license field until Shane decides (D-022).

```toml
[project]
name = "switchyard"
version = "0.1.0"
description = "LLM gateway engines: routing, fallback, caching and per-team budgets. Fake providers only. Not deployed."
readme = "README.md"
requires-python = ">=3.12,<3.13"
dependencies = [
    "pydantic==2.14.3",
    "typer==0.29.1",
    "pyyaml==6.0.3",
]

[project.scripts]
switchyard = "switchyard.cli:app"

[dependency-groups]
dev = [
    "pytest==9.2.0",
    "hypothesis==6.170.15",
    "ruff==0.17.5",
    "mypy==2.4.1",
    "types-PyYAML==6.0.12.20261113",
]

[build-system]
requires = ["hatchling==1.33.0"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/switchyard"]

[tool.ruff]
line-length = 110
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["src", "tests"]
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

Run `uv lock` once and commit `uv.lock`. If a pin does not resolve, stop and report the resolver's message, or
change that one pin and record it in `DECISIONS.md`. `.gitignore` copies Yardstick's and adds `runs/`.

## 3. CI (`.github/workflows/ci.yml`)

Same shape as Yardstick's `check` job. No secrets, no external request beyond installing packages.

```yaml
name: ci

on:
  push:
    branches: [main, prd, "phase-*"]
  pull_request:

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      - run: uv sync --locked
      - name: ruff
        run: uv run ruff check . && uv run ruff format --check .
      - name: mypy
        run: uv run mypy
      - name: pytest
        run: uv run pytest
      - name: example report is up to date
        run: uv run switchyard report fixtures/cases/c02-provider-outage.yaml -o examples/provider-outage-report.md && git diff --exit-code examples/
```

## 4. Modules

Every module is pure unless it says otherwise: no clock read, no I/O, no global state. "PRD x.y" points at the
rule the module implements. The PRD wins if this plan and the PRD disagree; record the fix.

### `money.py`

- **In / out.** `cost_micro(input_tokens: int, output_tokens: int, input_price: int, output_price: int) -> int`
  (PRD 3.1). `format_usd(micro: int) -> str`. A `Micro` annotated type (strict `int`, `>= 0` where used for
  prices, limits and costs) for pydantic models.
- **Invariants.** Result is an `int`. `cost * 1_000_000 >= exact numerator > (cost - 1) * 1_000_000`. Monotonic
  in both token counts. `format_usd` never uses float and round-trips through `parse_usd`.
- **Tests.** Table of exact cases including 292.4 → 293, 326.8 → 327 and 0 → 0. Hypothesis: the ceiling bound,
  monotonicity, `cost(a + b) <= cost(a) + cost(b)`. `bool`, `float` and `str` inputs raise. `format_usd(13200)`
  is `"0.013200"`; a negative value has a leading minus.

### `clock.py`

- **In / out.** `Clock` protocol: `now_ms() -> int`, `advance(ms: int) -> None`. `FakeClock(start_ms)`.
  `SystemClock` exists for Phase 2 and is the only place in `src/` allowed to read real time; its `advance` sleeps.
  Helpers: `parse_utc(str) -> int`, `format_utc(int) -> str` (millisecond ISO 8601 with `Z`), `period_of(ms) ->
  "YYYY-MM"`, `day_of(ms) -> "YYYY-MM-DD"`.
- **Invariants.** `FakeClock` never goes backwards. `advance` rejects negatives.
- **Tests.** Month and day boundaries at `2026-11-30T23:59:59.999Z` and `2026-12-01T00:00:00.000Z`, a leap day,
  parse and format round-trip.

### `canonical.py`

- **In / out.** `canonical_json(value) -> str` (sorted keys, no extra whitespace, UTF-8, no NaN) and
  `content_hash(value) -> "sha256:<hex>"`, the same rule as Yardstick's `hashing.py`.
- **Tests.** Key order does not change the hash. Floats are rejected.

### `models.py`

- **In / out.** Frozen pydantic v2 models, `extra="forbid"`, strict ints: `ModelSpec`, `Catalog`, `RouteRule`,
  `EngineSettings`, `RoutingPolicy`, `TeamBudget`, `TeamCache`, `TeamConfig`, `Message`, `Request`, `Reason`,
  `RouteDecision`, `LedgerEntry`, `Attempt`, `Event`, `Trace`, `ReportRow`. Enums: `Tier` (ordered), `Outcome`,
  `Status`, `EntryKind`, `Overage`, `CacheStatus`, `BreakerState`.
- **Invariants.** Validators enforce PRD AC-A6 and AC-C7: catch-all last rule, known tiers and tags, unique ids,
  `soft <= hard`, `overage_cap_micro > 0` exactly when `overage` is `allow_with_flag`, `min_tier` not above the
  rule's first tier, non-negative limits.
- **Tests.** One rejection test per validator. Every model round-trips through canonical JSON.

### `config_io.py` (does file I/O)

- **In / out.** `load_catalog(path)`, `load_policy(path)`, `load_teams(mapping)`; `config_version(policy,
  catalog) -> str`. YAML is loaded with a loader that rejects floats in integer fields and duplicate keys.
- **Tests.** The two illustrative files load. Each AC-A6 failure gives one clear error naming the file and field.

### `routing.py`

- **In / out.** `route(request, policy, catalog) -> RouteDecision` (PRD 4.1). `eligible(request, rule, catalog,
  tiers) -> (candidates, reasons)` is shared with the downgrade chain. `worst_case(request, model) -> int`.
- **Invariants.** AC-A1 to AC-A5. Reads nothing but its arguments.
- **Tests.** WE-01 to WE-05 as unit tests on the decision alone. Hypothesis over random catalogs, rules and
  requests: every model in the rule's tiers appears once in the reasons; the chain rebuilt from reasons matches;
  no uncleared model in a chain; chain order is (rule tier order, worst-case cost, model id); a tighter request
  limit never adds a candidate.

### `breaker.py`

- **In / out.** `CircuitBreaker(settings)` with `allow(now) -> Allow` (`allowed`, `is_probe`, `state`),
  `record(outcome, now)`, `give_back_probe()`, `state(now)`. `RetryBudget(settings)` with
  `note_first_attempt(now)`, `note_retry(now)`, `can_retry(now) -> bool`. `Health` holds one of each per provider
  (created on first use, iterated in provider-id order). A lock guards each object (PRD 5.2, 5.3).
- **Invariants.** AC-B2, AC-B3, AC-B5. State only changes inside `allow` and `record`. `record` without a
  matching `allow` raises.
- **Tests.** The `alder` timeline from WE-06 to WE-11 as a direct sequence of calls with the stated times and
  states. Boundaries: `open_ms - 1` refused, `open_ms` allowed; an outcome exactly `window_ms` old is dropped;
  3 failures of 3 do not open (`min_calls` 4); 2 failures of 4 open (50%); 1 of 4 does not. Hypothesis state
  machine over random allow/record/advance steps for AC-B2 and AC-B3. Retry budget: the WE-06 and WE-07 numbers,
  and `floor` behaviour at 4, 5 and 10 first attempts.

### `ledger.py` (holds state; one lock)

- **In / out.** `Ledger()` with `reserve(team, request_id, attempt, amount, now, limits) -> ReserveResult`
  (`accepted`, `entry` or `None`, `flags`, balances before), `settle(team, request_id, attempt, actual, now)`,
  `release(..., memo)`, `adjust(team, period, amount, now, memo)`, `sweep(now, ttl_ms)`,
  `balance(team, period) -> Balance(spent, held)`, `entries() -> tuple[LedgerEntry, ...]`,
  `to_jsonl()` / `from_jsonl()` (PRD 6.1, 6.2).
- **Invariants.** SW-02, SW-03, AC-C4 to AC-C6. The check and the append in `reserve` are one critical section.
- **Tests.** WE-14 to WE-17, WE-22, WE-23 at ledger level. Every `LedgerError` path appends nothing. Hypothesis:
  random interleavings of reserve and settle from up to 20 requests under each overage setting keep
  `spent + held <= ceiling` at every prefix, and balances recomputed from `entries()` equal `balance()`.
  Threaded test per AC-C3. JSON Lines round-trip.

### `budget.py`

- **In / out.** `ceiling(limits) -> int`; `check(balance, amount, limits) -> Decision` (`accept` with flags, or
  `refuse`); `downgrade_chain(request, rule, catalog, refused_model) -> list[model]` (PRD 6.2, 6.3).
- **Invariants.** Refuse exactly when `committed + amount > ceiling`. Flags exactly as PRD 6.2. The downgrade
  chain holds only models in tiers strictly below the refused model's and not below `min_tier`.
- **Tests.** Boundary table around soft, hard and ceiling (one below, equal, one above). WE-18 and WE-19 chains.
  `min_tier: premium` gives an empty downgrade chain.

### `providers/base.py`, `providers/fake.py`

- **In / out.** `Provider` protocol: `call(model, request, started_ms, timeout_ms) -> ProviderResult`
  (`outcome`, `latency_ms`, `input_tokens`, `output_tokens`, `text`). `FakeProvider(provider_id, catalog,
  script)`: a script is a list of windows `{from_ms, to_ms, outcome, latency_ms?, billed_input_tokens?,
  billed_output_tokens?}` relative to the case start; a call that starts in `[from_ms, to_ms)` follows the first
  matching window. Otherwise the result is `ok` after the model's declared latency, with
  `output_tokens = min(max_output_tokens, fake_output_tokens)`. The fake does not move the clock; the engine
  advances it by `latency_ms`. Response text is `"[fake <model>] <first 12 hex of the cache key>"`.
- **Invariants.** A latency above `timeout_ms` returns `timeout` with `latency_ms == timeout_ms`. Billed tokens
  never exceed the request's `input_tokens` and `max_output_tokens` (script validation). A successful call bills
  the request's `input_tokens`. No randomness, no network.
- **Tests.** Window edges (`from_ms` included, `to_ms` excluded). Timeout conversion. Script validation errors.

### `cache.py` (holds state; one lock)

- **In / out.** `normalize(messages)`, `cache_key(request, config_version) -> str`, `bypass_reason(request,
  team_cache) -> CacheStatus | None`, `ExactCache(max_entries)` with `get(team, key, now, ttl_ms)` and
  `put(team, key, entry, now)`, `size(team)` (PRD 7.1).
- **Invariants.** AC-D1 to AC-D6 (the store rule is applied by the engine; the cache refuses a `put` for a
  bypassed request as a second guard).
- **Tests.** WE-24 to WE-29 timings at cache level. Normalization table (CRLF, NFC, whitespace, role case, and
  inner whitespace and letter case that must stay different). Eviction order. Hypothesis: key equality exactly
  when normalized fields are equal.

### `engine.py`

- **In / out.** `Engine(policy, catalog, teams, providers, clock, ledger, health, cache)` with
  `handle(request, fake_output_tokens=None) -> Trace`. Implements PRD 3.7 and the loops in 5.3 and 6.3. The only
  module that advances the clock.
- **Invariants.** SW-01, SW-04, SW-05, SW-06. One trace per request. Every reserve it makes is settled before
  `handle` returns. A probe permit is always returned or resolved.
- **Tests.** Unit tests for each status. `failed_exhausted` when every breaker is open (no attempts, no ledger
  entries) and when `max_attempts` is reached. `deny` on a fallback attempt leaves earlier charges standing.
  Budget refusal during a half-open probe gives the permit back. Hypothesis: random request streams and outage
  scripts keep SW-02, SW-04, SW-05 and SW-06 and reconcile clean.

### `trace.py`

- **In / out.** `to_jsonl(traces)`, `from_jsonl(text)`, `reconcile(traces, ledger_entries) -> list[Difference]`
  (PRD 8.2).
- **Tests.** AC-E2, AC-E3 (mutation tests: change one cost, drop one ledger line, swap two `settle_seq` values).
  AC-E5: no message content appears in the JSON Lines of any golden case.

### `report.py`

- **In / out.** `daily_rows(traces, ledger_entries, teams) -> list[ReportRow]`, `render_markdown(rows)`,
  `render_json(rows)` (PRD 8.3). Calls `reconcile` first and raises `ReconcileError` on any difference.
- **Invariants.** AC-E4. Markdown says "illustrative prices, fake providers" in its first lines.
- **Tests.** WE-30. WE-23 puts cost on 2026-11-30. A request arriving before midnight with an attempt after it is
  counted as the PRD says. A tampered trace makes the report refuse.

### `cases.py`

- **In / out.** `load_case(path) -> Case`, `run_case(case) -> CaseResult` (traces, ledger, breaker states, cache
  sizes, report rows) for both case kinds in `fixtures/README.md`. Used by the golden tests and the CLI.
- **Tests.** A case whose `at_ms` is earlier than the previous request's finish is rejected. Unknown keys are
  rejected.

### `cli.py` (I/O)

- **In / out.** `switchyard replay CASE [--traces PATH] [--ledger PATH]` prints one line per request and exits 1
  if any `expect` block does not match; `switchyard explain CASE REQUEST_ID` prints the ordered reasons and
  events; `switchyard report CASE [-o PATH]` prints the daily report. Exit 2 on bad input.
- **Tests.** Typer runner tests for each command and each exit code.

## 5. Invariant tests (`tests/invariants/`)

| File | Proves |
|---|---|
| `test_sw01_determinism.py` | Each golden case run twice gives byte-identical trace, ledger and report text |
| `test_sw02_no_overspend.py` | Hypothesis interleavings and the threaded test (AC-C1, AC-C3) |
| `test_sw03_ledger_integrity.py` | Gapless `seq`, each reserve closed once, `settle <= reserve`, no mutation API |
| `test_sw04_reconciliation.py` | `reconcile` is empty for golden and generated runs, and non-empty after any single mutation |
| `test_sw05_sensitive.py` | Tagged requests never reach an uncleared model or the cache |
| `test_sw06_no_retry_storm.py` | Breaker and retry-budget bounds under generated outage scripts |
| `test_sw07_integer_money.py` | Money fields reject floats; an AST scan of `src/switchyard` finds no `float(` call, float literal or `/` true division in `money.py`, `ledger.py`, `budget.py`, `report.py` |
| `test_sw08_explainable.py` | Reasons are complete and rebuild the chain |
| `test_sw09_fakes_only.py` | An AST scan of `src/switchyard` finds no import of a network or provider module and no environment read |

## 6. Golden tests

`tests/golden/test_worked_examples.py` is parametrized over `fixtures/cases/*.yaml`. For each request or step it
compares the result with the `expect` block, and for each case the `expect_end` block. A second test checks that
every ID from WE-01 to WE-30 appears in exactly one case, so no worked example can be dropped quietly.

## 7. Risks and how to handle them

- **A worked example and the code disagree.** Do not edit the expected value. Recompute by hand from PRD
  sections 3 to 8. If the PRD table is wrong, fix the PRD, add a `DECISIONS.md` row and list it in the pull
  request.
- **Two readings of a rule.** The PRD text wins over this plan, and a worked example wins over prose that could be
  read two ways. Record the reading.
- **Threaded test flakiness.** Assert only counts and the invariant.
- **Scope creep.** No HTTP, no HTML, no `policy diff`, no semantic cache, no real tokenizer in this phase.

## 8. Pull request for Phase 1

Draft, titled "switchyard: Phase 1 pure engines". Body: what was built, gate results with real output, the
worked examples reproduced (count and any PRD corrections), new `DECISIONS.md` rows, "Not verified", and "You:"
steps. Then stop. Do not start Phase 2.
