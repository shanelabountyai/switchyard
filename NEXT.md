# NEXT: Switchyard

## 2026-11-27: Phase 2 plan written (unattended run, branch `phase-2-plan`)

**Phase 2 plan written; next run builds Phase 2 on these defaults unless Shane changes a D-row.**

This run wrote a plan and built nothing: no product code, no change to `pyproject.toml` or `uv.lock`. **Nothing
is deployed. Every provider is a fake. No API key, no network call, no live model call. Prices are
illustrative.** Pull requests #1 (PRD and kickoff kit) and #2 (Phase 1) were merged on 2026-11-27; `main` was
`ac9e3de` when this run started.

### What was written

| Path | What it is |
|---|---|
| `docs/switchyard/PHASE-2-PLAN.md` | The Phase 2 plan: 22 exit criteria (X1 to X22), the HTTP contract, the changes to Phase 1 modules, new modules, build order, how the build stays offline, open items and their defaults, "Not in Phase 2" |
| `docs/switchyard/DECISIONS.md` | Rows D-047 to D-062 (16 rows). No earlier row was edited |
| `NEXT.md` | This section |

### The choices that matter most

- **HTTP library: Werkzeug, pinned `werkzeug==3.1.9`** (D-048). Compared with FastAPI 0.165.0 and Flask 3.1.3.
  Not yet added to `pyproject.toml`.
- **"Identical outputs" for the golden replay** (D-055): the 14 sequential cases must give byte-identical trace
  and ledger files through HTTP; `c07` is replayed with three real overlapping requests held by a test gate.
- **Four small engine changes for real concurrency** (D-056), guarded by "every Phase 1 test passes unedited".
- **`max_tokens` is required** (D-049) and **default port 8600 is a proposal** (D-058).

### Gate results (local, this run, on the docs-only branch)

- `uv sync --locked`: ok.
- `uv run ruff check .`: All checks passed!
- `uv run ruff format --check .`: 61 files already formatted (60 before this run; the new plan file is counted).
- `uv run mypy` (strict): Success: no issues found in 51 source files.
- `uv run pytest`: 284 passed.
- Example report drift step: no diff.
- CI on GitHub: see the pull request body.

### Independent review

One fresh agent saw only the repository, the PRD and the plan. It reported 6 must-fix, 14 should-fix and 12
nits. All 6 must-fix and all 14 should-fix were acted on. What was not changed, and why, is D-062.

### What could not be done or checked here

- The Yardstick repository was not in this session's scope, so its `http` target format was not read (D-059).
- The library comparison used a throwaway project that is not committed. Its package counts cannot be re-run
  from this repository.
- The reviewer did not re-review the fixes made after its report.
- Shane's port table could not be read (D-058).

### What is open

1. The "Shane to confirm" rows: D-012, D-014, D-015, D-016, D-022, D-038, and now D-058 (the port). Each has a
   default that stands; `PHASE-2-PLAN.md` section 12 lists them.
2. Exit criterion X21 (a real Yardstick run against the local gateway) may end up as a hand step.

### What the next run does: build Phase 2

Follow `docs/switchyard/PHASE-2-PLAN.md` section 9, on branch `phase-2` cut from `main`, one draft pull request
"switchyard: Phase 2 HTTP gateway (fakes only)". Step 1 re-reads `DECISIONS.md` for any row Shane changed and
reads Yardstick's `http` target format. Stop at the Phase 2 gate. Do not start Phase 3.

## Shane's hand steps

- You: read `docs/switchyard/PHASE-2-PLAN.md` sections 2, 3 and 5 first. They hold the exit criteria, the
  library choice and the HTTP contract.
- You: if you want FastAPI or Flask instead of Werkzeug, add a `DECISIONS.md` row that replaces D-048 before the
  build run.
- You: confirm or change port 8600 against your port table (D-058).
- You: say whether `max_tokens` may be required (D-049). If Yardstick's `http` target cannot send it, the build
  run will stop and ask.
- You: add the Yardstick repository to the build run's scope so it can read the `http` target format (D-059).
- You: answer D-038 and the earlier "Shane to confirm" rows (D-012, D-014, D-015, D-016, D-022) when you can.
  The build uses the defaults until you do.

## Earlier runs

### 2026-11-26: Phase 1 pure engines (unattended run, branch `kickoff`)

Kept as written at the time. Both pull requests it mentions have since been merged.

Shane was away (Thanksgiving). This run built Phase 1 on branch `kickoff`, cut from `prd`, and opened one draft
pull request, "switchyard: Phase 1 (pure engines)", against `prd`. Not merged, not marked ready. **Nothing is
deployed. Every provider is a fake. No API key, no network call, no live model call. Prices are illustrative.**

#### What was built

| Path | What it is |
|---|---|
| `pyproject.toml`, `uv.lock` | Python 3.12, uv; pins copied from Yardstick `710c604` (pydantic, typer, pyyaml; pytest, hypothesis, ruff, mypy, types-PyYAML) |
| `.github/workflows/ci.yml` | Yardstick's `check` job shape: `uv sync --locked`, ruff, ruff format, mypy, pytest, example-report drift check |
| `fixtures/catalog/`, `fixtures/policy/` | The illustrative catalog (PRD 3.3) and policy (PRD 3.4, 3.5) |
| `fixtures/cases/` | 15 golden cases covering WE-01 to WE-30 |
| `src/switchyard/` | money, clock, canonical, models, config_io, routing, breaker, ledger, budget, cache, providers (fake only), engine, trace (reconcile), report, cases, cli |
| `tests/` | unit tests per module, `golden/` (every worked example), `invariants/` (SW-01 to SW-09 plus a test that the generators reach every path) |
| `examples/provider-outage-report.md` | Daily report written by `switchyard report` from c02; CI fails if it drifts |

#### Gate results (local, this run)

- `uv sync --locked`: ok (27 packages checked).
- `uv run ruff check .`: All checks passed!
- `uv run ruff format --check .`: 61 files already formatted (60 before this run; the new plan file is counted).
- `uv run mypy` (strict): Success: no issues found in 51 source files.
- `uv run pytest`: 284 passed. All 30 worked-example tests pass (30/30). No expected value was edited.
- CI on GitHub: see the pull request body.

#### What is open

1. Shane's review of the PRD pull request (#1), then of the Phase 1 pull request.
2. New decision rows D-034 to D-046. D-038 (the AC-C2 wording) is marked "Shane to confirm".
3. The earlier "Shane to confirm" rows: D-012, D-014, D-015, D-016, D-022 (build-log number, license).
4. Review items not done, listed in the pull request: retry-budget lists are never pruned; WE-18's downgrade
   models and WE-27's re-store time are asserted only in unit tests, not in the case files.

#### What the next run does: Phase 2 (only after Shane says so)

Write `docs/switchyard/PHASE-2-PLAN.md` first (HTTP framework choice and pin as a decision row), then the HTTP
front on `127.0.0.1` per `IMPLEMENTATION_PLAN.md`. The 15 golden cases must replay through HTTP unchanged.
