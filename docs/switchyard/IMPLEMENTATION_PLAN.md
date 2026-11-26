# Switchyard: implementation plan

Four phases. Each is one branch, one draft pull request and one stop. Nothing is deployed in any phase. Every
provider is a fake in every phase. The spec is `PRD.md`. Decisions are `DECISIONS.md`.

## Rules for every phase

- Branch `phase-N`. Draft pull request. Claude never merges, never marks ready, never force-pushes.
- Gates: `uv sync --locked`, `ruff check`, `ruff format --check`, `mypy` (strict), `pytest`. All green before the
  pull request is updated. A failing gate is fixed or reported, never loosened.
- The invariants SW-01 to SW-09 (PRD section 10) and the 30 worked examples (PRD section 11) stay green in every
  later phase.
- Each phase starts by writing `docs/switchyard/PHASE-N-PLAN.md` (Phase 1's is already written), and ends by
  updating `NEXT.md`.
- New judgment calls are new rows in `DECISIONS.md` and in the pull request body.

## Phase 1: pure engines

**Goal.** Routing, fallback and per-team budget logic as pure, deterministic code with property and golden
tests. Cache, trace, reconcile and the daily report are included because the worked examples need them. No HTTP
server.

**Deliverables.** `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `.gitignore`, `src/switchyard/`,
`tests/`, `fixtures/catalog/`, `fixtures/policy/`, `fixtures/cases/` (15 case files), an example report under
`examples/` produced by the CLI. Detail: `PHASE-1-PLAN.md`.

**Exit gate.** CI green on the branch. SW-01 to SW-09 green. All 30 worked examples reproduced by golden tests
with no expected value edited (or each edit explained in a `DECISIONS.md` row).

**Order of work.** money → clock → models and config loading → routing → ledger and budget → breaker and retry
budget → fake provider → cache → engine → trace and reconcile → report → CLI. Golden case files are written from
the PRD table before the engine, so the engine is built against them.

## Phase 2: HTTP gateway against fakes

**Goal.** Put an HTTP front on the Phase 1 engine.

- `POST /v1/chat/completions` with an OpenAI-compatible request and response shape (`model`, `messages`,
  `max_tokens`, `temperature` in; `choices`, `usage` out). The request's `model` field is treated as a hint and
  recorded. The routing policy decides the model.
- Team, app, use case, task class and data tags come from request headers (`X-Switchyard-Team` and so on). There
  is no authentication. The server binds to `127.0.0.1` only and refuses any other bind address.
- A deterministic fake tokenizer counts `input_tokens` (documented rule, for example one token per four
  characters, rounded up). It is labelled a fake.
- Response extras: `latency_ms`, `cost_usd` (six-decimal string), route and cache status, so a Yardstick `http`
  target can read them.
- Errors map to HTTP statuses: `denied_budget` 429 with a machine-readable body, `no_route` 422,
  `failed_exhausted` 503, `bad_request` 400, `unknown_team` 403.
- The ledger and traces are also written as JSON Lines files. Real concurrency arrives here, so the ledger lock
  and the half-open probe permit are exercised by concurrent HTTP tests.
- The framework and its pinned version are chosen in `PHASE-2-PLAN.md` with a `DECISIONS.md` row. The HTTP code
  lives in one `server` package, the only place SW-09 lets a network module be imported. Tests use an
  in-process client. CI makes no external request.

**Exit gate.** The 15 golden cases replayed through HTTP give the same traces and ledger as Phase 1 (clock still
injected). A concurrent test fires many requests at a nearly spent budget and SW-02 holds. A Yardstick `http`
target config that points at the local gateway is committed and has been run locally against it.

## Phase 3: admin views and reports

**Goal.** Let each of the four users see their answer without reading JSON.

- Static HTML built from the ledger and trace files, no CDN and no server-side state: budget view (period-to-date
  spent, held, limits, flags, per team), breaker timeline (state changes and skipped calls per provider), trace
  explorer (one request: route reasons, events, attempts, charges with ledger `seq` links), daily cost report.
- `switchyard policy diff old.yaml new.yaml`: for a set of sample requests, the planned model and worst-case
  cost before and after. This is the input to the Yardstick hand-off (PRD section 9), which stays a design.
- `switchyard sweep` for stale reserves, and a report section for `unsettled` holds and adjustments.

**Exit gate.** Every number on a view is traced by a test to a ledger or trace line. Views render from the golden
cases offline. Accessibility basics checked (headings, table headers, contrast, keyboard focus).

## Phase 4: demo and closeout

**Goal.** A hiring manager can follow it in ten minutes from a clean clone, offline.

- `docs/switchyard/DEMO.md`: a provider outage with the breaker opening and closing, a team hitting its soft
  then hard limit under each overage policy, a cache hit, and a route change with its cost difference.
- README with a quick start, the "fakes only, not deployed, illustrative prices" statement near the top, and how
  Switchyard and Yardstick fit together.
- Release notes with known gaps. An independent review. `NEXT.md` closed out.

**Exit gate.** The demo runs from a fresh clone with only `uv` installed. Every command and output in the README
and demo was produced by a run and is marked as such.

## Risks

| Risk | Where | Mitigation |
|---|---|---|
| A worked example in the PRD is wrong | Phase 1 | The run stops on a mismatch, works out which side is wrong, and records a PRD fix as a decision. Expected values are never edited silently |
| Hidden nondeterminism (dict or set order, thread timing) | Phase 1, 2 | Canonical JSON with sorted keys, sorted iteration, SW-01 run twice in one test and compared byte for byte |
| Float sneaks into money through YAML (`0.5`) or JSON | Phase 1 | Strict integer types at the model boundary, AC-C7, AC-A6 |
| A pinned version copied from Yardstick does not resolve | Phase 1 | `uv lock` fails loudly. Stop and report, or change the pin with a `DECISIONS.md` row |
| The threaded ledger test is flaky | Phase 1 | It asserts only the invariant (count accepted, no overspend), never an order |
| Phase 2 concurrency breaks the "one request at a time" assumption in the engine | Phase 2 | Engine state lives in the ledger, breaker and cache, each with its own lock and tested interleavings. Golden replay stays sequential |
| The project reads as a product claim | All | CLAUDE.md honesty rules. "Illustrative" and "fake" labels checked in review |
