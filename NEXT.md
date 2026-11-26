# NEXT: Switchyard

## 2026-11-26: Phase 1 pure engines (unattended run, branch `kickoff`)

Shane was away (Thanksgiving). This run built Phase 1 on branch `kickoff`, cut from `prd`, and opened one draft
pull request, "switchyard: Phase 1 (pure engines)", against `prd`. Not merged, not marked ready. **Nothing is
deployed. Every provider is a fake. No API key, no network call, no live model call. Prices are illustrative.**

### What was built

| Path | What it is |
|---|---|
| `pyproject.toml`, `uv.lock` | Python 3.12, uv; pins copied from Yardstick `710c604` (pydantic, typer, pyyaml; pytest, hypothesis, ruff, mypy, types-PyYAML) |
| `.github/workflows/ci.yml` | Yardstick's `check` job shape: `uv sync --locked`, ruff, ruff format, mypy, pytest, example-report drift check |
| `fixtures/catalog/`, `fixtures/policy/` | The illustrative catalog (PRD 3.3) and policy (PRD 3.4, 3.5) |
| `fixtures/cases/` | 15 golden cases covering WE-01 to WE-30 |
| `src/switchyard/` | money, clock, canonical, models, config_io, routing, breaker, ledger, budget, cache, providers (fake only), engine, trace (reconcile), report, cases, cli |
| `tests/` | unit tests per module, `golden/` (every worked example), `invariants/` (SW-01 to SW-09 plus a test that the generators reach every path) |
| `examples/provider-outage-report.md` | Daily report written by `switchyard report` from c02; CI fails if it drifts |

### Gate results (local, this run)

- `uv sync --locked`: ok (27 packages checked).
- `uv run ruff check .`: All checks passed!
- `uv run ruff format --check .`: 60 files already formatted.
- `uv run mypy` (strict): Success: no issues found in 51 source files.
- `uv run pytest`: 284 passed. All 30 worked-example tests pass (30/30). No expected value was edited.
- CI on GitHub: see the pull request body.

### What is open

1. Shane's review of the PRD pull request (#1), then of the Phase 1 pull request.
2. New decision rows D-034 to D-046. D-038 (the AC-C2 wording) is marked "Shane to confirm".
3. The earlier "Shane to confirm" rows: D-012, D-014, D-015, D-016, D-022 (build-log number, license).
4. Review items not done, listed in the pull request: retry-budget lists are never pruned; WE-18's downgrade
   models and WE-27's re-store time are asserted only in unit tests, not in the case files.

### What the next run does: Phase 2 (only after Shane says so)

Write `docs/switchyard/PHASE-2-PLAN.md` first (HTTP framework choice and pin as a decision row), then the HTTP
front on `127.0.0.1` per `IMPLEMENTATION_PLAN.md`. The 15 golden cases must replay through HTTP unchanged.

## Shane's hand steps

- You: review and merge (or change) PR #1, "switchyard: PRD + kickoff kit", first. The Phase 1 PR is based on it.
- You: read the Phase 1 draft PR. Its body lists each acceptance item with the test that proves it.
- You: answer D-038 and the earlier "Shane to confirm" rows. If you reverse one, name the row for the next run.
- You: decide the build-log number and license (D-022).
- You: when both PRs are merged, retarget nothing by hand: the Phase 1 PR's base is `prd`; after merging #1,
  change the Phase 1 PR's base to `main` in the GitHub UI before merging it.
