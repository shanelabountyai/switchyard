# Switchyard: rules for Claude Code

Switchyard is an LLM gateway: routing, fallback, caching and per-team budgets. A Lab Intelligence build-log
project by Shane LaBounty, written as a portfolio piece. Read these before doing anything:

1. `NEXT.md`: where the project is and what to do next.
2. `docs/switchyard/PRD.md`: the spec, the acceptance criteria and the 30 worked examples.
3. `docs/switchyard/DECISIONS.md`: decisions in force (D-001 onward).
4. `docs/switchyard/IMPLEMENTATION_PLAN.md` and the plan for the current phase (`PHASE-1-PLAN.md`).
5. `fixtures/README.md`: the golden case format.

## Honesty rules (never break these)

- **Fakes stay fakes.** Every provider is a fake adapter with scripted latency, errors, token counts and prices.
  Never add a real provider client, a real endpoint or a provider SDK.
- **Never add a real API key**, token or secret. No `.env` file. The package reads no environment variable and
  opens no network connection. Phase 2 binds to `127.0.0.1` only.
- **No live model calls**, anywhere: not locally, not in CI, not in a test.
- **Not deployed.** Never write that Switchyard is live, launched, shipped, deployed or in production. Never
  deploy it, host it or create an account for it.
- **No invented numbers.** No benchmarks, performance figures, savings percentages, adoption figures or
  testimonials. A number in this repo is either computed by code in this repo or labelled illustrative.
- **Prices are illustrative.** They live in a data file (`fixtures/catalog/illustrative-catalog.yaml`), are
  labelled illustrative wherever they are shown, and use fictional provider names (`alder`, `birch`, `cedar`).
  Never present a real vendor's price, latency or quality as fact. Never name a real vendor's model in the catalog.
- Report what happened. If a test fails or a step was skipped, say so with the output. "Not verified" is a
  normal section of every pull request.

## Engineering rules

- Python 3.12, `src/` layout, uv, pydantic v2, typer, pytest, hypothesis, ruff, mypy `--strict`. Versions are
  pinned in `pyproject.toml` and copied from Yardstick's `pyproject.toml`. Do not bump a pin without a
  `DECISIONS.md` row.
- **Money is integer micro-dollars.** No float in any money path. See PRD section 3.1 for the one rounding rule.
- **Time comes from the injected clock.** No `time.time()`, `datetime.now()` or `sleep` in `src/` outside
  `clock.py`.
- **Deterministic.** No randomness in `src/`. Iterate in a defined order (sort, or keep insertion order on
  purpose). The same inputs give byte-identical outputs.
- The ledger is append-only. No method edits or deletes an entry.
- The worked examples in PRD section 11 are a contract. If code and a worked example disagree, stop and work out
  which is wrong. If the PRD is wrong, fix the PRD in the same pull request, add a `DECISIONS.md` row that says
  what changed and why, and say so in the pull request body. Never edit an expected value just to make a test pass.
- Gates before any pull request update: `uv sync --locked`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`. If a gate fails, fix the cause or stop and
  report. Never loosen a gate, skip a test or delete an assertion to get green.

## Working rules

- `main` gets no direct commits. Work on a branch (`phase-N`), open one **draft** pull request, never merge it,
  never mark it ready for review, never force-push.
- When a decision is needed and Shane is not there: take the most reasonable option, add the next numbered row
  to `docs/switchyard/DECISIONS.md` (problem, options, decision, how to change it), repeat it in the pull request
  body, and keep going. Stop only for something that cannot be undone and could reasonably go either way.
- Stay inside the current phase. Do not start the next one.
- Update `NEXT.md` at the end of every run: what was built, gate results with real output, what is open, and
  Shane's hand steps, each starting "You:".
- Plain language in docs and pull requests. Say what a thing does, then how.

## How Switchyard and Yardstick fit

Switchyard governs spend and reliability. [Yardstick](https://github.com/shanelabountyai/yardstick) measures
quality. Switchyard never claims an answer is good. PRD section 9 describes a design (not built) for gating a
route change on a Yardstick run.
