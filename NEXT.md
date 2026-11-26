# NEXT: Switchyard

## 2026-11-26: PRD and kickoff kit (unattended run, branch `prd`)

Shane was away. This run wrote the spec and the kit a build run needs. **No application code exists yet.
Nothing is deployed. No API key, no live model call, no real vendor price.** One draft pull request,
"switchyard: PRD + kickoff kit", against `main`. Not merged.

### What is on `prd`

| File | What it is |
|---|---|
| `CLAUDE.md` | Project rules and honesty rules. Read first |
| `docs/switchyard/PRD.md` | Problem, users, six capabilities with acceptance criteria, invariants SW-01 to SW-09, 30 worked examples, non-goals, open questions, phase plan |
| `docs/switchyard/DECISIONS.md` | Judgment calls, D-001 onward |
| `docs/switchyard/IMPLEMENTATION_PLAN.md` | The four phases, gates and risks |
| `docs/switchyard/PHASE-1-PLAN.md` | Package layout, each module's inputs, outputs, invariants and tests, `pyproject.toml`, CI |
| `fixtures/README.md` | The 15 golden cases and their file format |
| `NEXT.md` | This file |

`main` holds only the placeholder `README.md`.

### State of checks

No code, so no ruff, mypy or pytest run exists. The 30 worked examples were computed by a throwaway script in
the run's sandbox (not committed, D-024). Phase 1's golden tests are the real check. An independent review of
the PRD and plans was run; its findings and what was done are in the pull request body.

## What the next run does: Phase 1

Read `CLAUDE.md`, then `docs/switchyard/PRD.md`, `DECISIONS.md`, `PHASE-1-PLAN.md` and `fixtures/README.md`.
Where the run's own prompt differs from this file, the prompt wins.

1. Branch `phase-1` from `prd` if the kit pull request is still unmerged, otherwise from `main` (D-023). Never
   commit to `main` or `prd`.
2. Scaffold per `PHASE-1-PLAN.md` sections 1 to 3: `pyproject.toml` with the pinned versions, `uv lock`, CI,
   `.gitignore`.
3. Write the catalog, the policy and the 15 case files from the PRD tables **before** the engine.
4. Build the modules in the order in `IMPLEMENTATION_PLAN.md`, tests with each module.
5. Gates: `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
   `uv run pytest`. All green, SW-01 to SW-09 green, all 30 worked examples reproduced.
6. Open one draft pull request, "switchyard: Phase 1 pure engines", against `prd` (or `main` if the kit was
   merged). Body per `PHASE-1-PLAN.md` section 8. Update this file. Stop. Do not start Phase 2.

If a worked example and the code disagree, do not edit the expected value to get green. Work out which is
wrong, and if it is the PRD, fix the PRD with a `DECISIONS.md` row.

## What is open

1. Shane's review of the kit pull request.
2. The decisions marked "Shane to confirm" in `DECISIONS.md` (also listed in the pull request).
3. PRD section 13, open questions Q1 to Q13. Each has a default that Phase 1 builds.
4. Build-log number and license (D-022).

## Shane's hand steps

- You: read the draft pull request "switchyard: PRD + kickoff kit". Merge it or ask for changes. Claude does not
  merge.
- You: answer the "Decisions for Shane to confirm" in the pull request. Start with the budget hold question
  (D-012), because it shapes the ledger and the engine.
- You: skim the PRD's worked-example table (section 11) and say if any expected behaviour is not what you want.
  It is cheaper to change now than after Phase 1.
- You: decide the build-log number and the license. If MIT like Yardstick, say so and Phase 1 adds the `LICENSE`
  file.
- You: if the Phase 1 run has already built against a default you change, tell the next run which decision row
  to reverse.
