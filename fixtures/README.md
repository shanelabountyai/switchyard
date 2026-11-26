# Fixtures

Everything in this folder is **illustrative**. The providers (`alder`, `birch`, `cedar`) are fictional. The
prices, latencies, token counts and budgets are made up so the examples are easy to follow. None of it describes
a real vendor.

Only this README exists on the `prd` branch. Phase 1 creates the data files it describes
(`docs/switchyard/PHASE-1-PLAN.md`).

## Files

| Path | What it is |
|---|---|
| `catalog/illustrative-catalog.yaml` | The six models in PRD section 3.3: provider, tier, input and output price (µUSD per 1M tokens), declared latency, cleared tags. First line: `# ILLUSTRATIVE. Fictional providers and made-up prices.` |
| `policy/illustrative-policy.yaml` | The four rules in PRD section 3.4 and the `engine` block in PRD section 3.5 |
| `cases/cNN-name.yaml` | One golden case each. Fifteen cases cover the thirty worked examples in PRD section 11 |

## Golden cases

A case is one starting state plus an ordered list of things that happen, each with the exact result expected.
The golden test runs the case and compares every expected value. Times are milliseconds after `start_at`.

| Case | Kind | Worked examples | What it shows |
|---|---|---|---|
| `c01-routing-basics` | sequential | WE-01 to WE-05 | Rule matching, clearance, cost and latency exclusions, no route, the ceiling rule |
| `c02-provider-outage` | sequential | WE-06 to WE-11, WE-30 | **The provider-outage case.** Retry, retry budget, breaker opening, skipping, a failed probe, recovery, and the daily report |
| `c03-attempt-errors` | sequential | WE-12, WE-13 | A timeout that billed tokens then a retry; a `bad_request` |
| `c04-budget-soft-limit` | sequential | WE-14 | Soft limit flags and does not block |
| `c05-budget-hard-deny` | sequential | WE-15 | Hard limit with `deny` |
| `c06-budget-boundary` | sequential | WE-16 | A reserve that lands exactly on the limit is accepted |
| `c07-concurrent-overspend` | interleaved | WE-17 | **The concurrent-overspend case.** A hold blocks a second request, then frees room for a third |
| `c08-budget-downgrade` | sequential | WE-18 | `downgrade` to a lower tier; the answer is not cached |
| `c09-budget-downgrade-denied` | sequential | WE-19 | `downgrade` with no tier that fits |
| `c10-budget-allow-flag` | sequential | WE-20, WE-21 | `allow_with_flag` over the hard limit, then refused at the cap |
| `c11-period-rollover` | sequential | WE-22 | Refused in November, accepted one second later in December |
| `c12-period-straddle` | sequential | WE-23 | Reserved before midnight, settled after: posts to the reserve's month and day |
| `c13-cache-exact` | sequential | WE-24 to WE-27 | Miss, hit after normalization, hit one millisecond before TTL, miss at TTL |
| `c14-cache-sensitive` | sequential | WE-28 | A tagged request is never cached |
| `c15-cache-opt-out` | sequential | WE-29 | A team with the cache switched off |

Defaults for every case unless its file says otherwise: `start_at: "2026-11-02T09:00:00.000Z"`; budgets far from
their limits (soft and hard both `1000000000000`, `deny`); cache **off** for every team. Cases `c08`, `c13` and
`c14` turn the cache on with `ttl_ms: 60000`. `c11` starts at `2026-11-30T23:59:59.000Z` and `c12` at
`2026-11-30T23:59:59.900Z`.

### Kind `sequential`

Requests run one at a time through the whole engine. A request's `at_ms` must not be earlier than the time the
previous request finished. The loader rejects a case that breaks this.

```yaml
id: c02-provider-outage
kind: sequential
illustrative: true
description: alder fails for 20 seconds; support keeps sending the same triage request.
start_at: "2026-11-02T09:00:00.000Z"
catalog: ../catalog/illustrative-catalog.yaml
policy: ../policy/illustrative-policy.yaml
teams:
  support:
    budget: {period: calendar_month_utc, soft_limit_micro: 1000000000000, hard_limit_micro: 1000000000000,
             overage: deny, overage_cap_micro: 0}
    cache: {enabled: false, ttl_ms: 60000}
opening: []            # optional: [{team, period, amount_micro, memo}] written as ledger adjustments
scripts:
  alder:
    - {from_ms: 0, to_ms: 20000, outcome: server_error, latency_ms: 50}
requests:
  - we: WE-06
    at_ms: 0
    fake_output_tokens: 12
    request:
      request_id: r1
      team: support
      app: helpdesk
      use_case: triage
      task_class: classify
      data_tags: []
      messages: [{role: user, content: "Illustrative ticket text 1"}]
      input_tokens: 400
      max_output_tokens: 50
      temperature: "0"
    expect:
      status: ok
      rule: support-triage
      chain: [alder-small, birch-small]
      cache: bypass_team_opt_out
      attempts:
        - {model: alder-small, started_ms: 0, finished_ms: 50, outcome: server_error, reserved_micro: 120, cost_micro: 0}
        - {model: alder-small, started_ms: 250, finished_ms: 300, outcome: server_error, reserved_micro: 120, cost_micro: 0}
        - {model: birch-small, started_ms: 300, finished_ms: 600, outcome: ok, reserved_micro: 150, cost_micro: 112}
      events: [retry, no_retry:per_request_retry_limit]
      flags: [fallback_used]
      cost_micro: 112
  # … WE-07 to WE-11
expect_end:
  balances:
    - {team: support, period: "2026-11", spent_micro: 650, held_micro: 0}
  breakers: {alder: closed, birch: closed}
  ledger_entries: 22
  cache_entries: {support: 0}
  reconcile: clean
  report:
    - {date: "2026-11-02", team: support, requests: 6, ok: 6, cache_hits: 0, attempts: 11, failed_attempts: 5,
       input_tokens: 2400, output_tokens: 72, cost_micro: 650, cost_usd: "0.000650"}
```

Rules for the file:

- `scripts`: per provider, a list of windows. A call that **starts** in `[from_ms, to_ms)` follows the window:
  `outcome` (`ok`, `timeout`, `server_error`, `rate_limited`, `bad_request`), optional `latency_ms`, and optional
  `billed_input_tokens` and `billed_output_tokens`, which apply only when the final outcome is a failure. With no
  matching window the call succeeds after the model's declared latency.
- A latency above the attempt timeout turns the outcome into `timeout` after exactly the timeout. WE-12 uses
  `{from_ms: 0, to_ms: 2100, outcome: ok, latency_ms: 9999, billed_input_tokens: 1000, billed_output_tokens: 80}`.
- `fake_output_tokens`: what a successful call returns, capped at `max_output_tokens`.
- `expect.events` lists event codes in order, with `no_retry` written as `no_retry:<reason>`. `expect.route_reasons`
  is optional and, where present, lists `code:subject` in order (for example `excluded_cost:birch-medium`). It is
  present for WE-01 to WE-05, WE-18 and WE-21.
- Every money value is a whole number of micro-dollars. A float anywhere in a case file is a load error.
- `expect_end.reconcile: clean` is required in every sequential case.

### Kind `interleaved`

Used for the concurrent-overspend case. Steps act on the ledger directly, in the order written, so the test
controls exactly how two requests overlap. Each `reserve` step names a model and token counts, and the amount is
computed with the same worst-case formula the engine uses.

```yaml
id: c07-concurrent-overspend
kind: interleaved
illustrative: true
start_at: "2026-11-02T09:00:00.000Z"
catalog: ../catalog/illustrative-catalog.yaml
teams:
  support:
    budget: {period: calendar_month_utc, soft_limit_micro: 8000, hard_limit_micro: 10000,
             overage: deny, overage_cap_micro: 0}
    cache: {enabled: false, ttl_ms: 60000}
opening:
  - {team: support, period: "2026-11", amount_micro: 9790, memo: "illustrative opening balance"}
steps:
  - {op: reserve, at_ms: 0, team: support, request_id: A, attempt: 1, model: alder-small, input_tokens: 400, max_output_tokens: 50,
     expect: {accepted: true, amount_micro: 120, flags: [soft_limit_crossed], spent_micro: 9790, held_micro: 120}}
  - {op: reserve, at_ms: 1, team: support, request_id: B, attempt: 1, model: alder-small, input_tokens: 400, max_output_tokens: 50,
     expect: {accepted: false, amount_micro: 120, spent_micro: 9790, held_micro: 120}}
  - {op: settle, at_ms: 400, team: support, request_id: A, attempt: 1, model: alder-small, input_tokens: 400, output_tokens: 12,
     expect: {amount_micro: 90, spent_micro: 9880, held_micro: 0}}
  - {op: reserve, at_ms: 401, team: support, request_id: C, attempt: 1, model: alder-small, input_tokens: 400, max_output_tokens: 50,
     expect: {accepted: true, amount_micro: 120, flags: [soft_limit_crossed], spent_micro: 9880, held_micro: 120}}
  - {op: settle, at_ms: 801, team: support, request_id: C, attempt: 1, model: alder-small, input_tokens: 400, output_tokens: 12,
     expect: {amount_micro: 90, spent_micro: 9970, held_micro: 0}}
expect_end:
  balances:
    - {team: support, period: "2026-11", spent_micro: 9970, held_micro: 0}
  ledger_entries: 5      # 1 adjustment, 2 reserves, 2 settles. The refused reserve writes nothing.
```

`spent_micro` and `held_micro` in an `expect` block are the balances **after** the step.

## Adding a case

1. Work the numbers by hand from PRD section 3 first, then write the `expect` blocks.
2. Add the worked example to the PRD table if it shows something new.
3. Never change an expected value to match the code without a `DECISIONS.md` row that says why the old value
   was wrong.
