# Switchyard: PRD

A Lab Intelligence build-log project. Repo: `shanelabountyai/switchyard` (public).
Base: **standalone Python 3.12 service/library**, same toolchain and CI shape as
[Yardstick](https://github.com/shanelabountyai/yardstick) (uv, pydantic, typer, pytest, hypothesis, ruff, mypy).

Status: **spec only. Nothing is built and nothing is deployed.** Every provider in this project is a fake adapter
with scripted behaviour. No API key is used anywhere. Every price, budget, latency and token count in this
document is **illustrative**: made up for the examples, not a claim about any real vendor.

Written 2026-11-26 in an unattended run. Judgment calls are rows in `DECISIONS.md` (D-001 onward).

## 1. Problem

Teams call model providers directly from their own apps. Three things follow:

1. **Nobody can cap spend.** Each app holds its own key. A budget exists on a slide, not in the request path.
2. **Nobody survives a provider outage.** Each app has its own retry loop, or none. When a provider degrades, the
   apps retry in step and make it worse.
3. **Nobody can explain a bill.** The invoice is one number per provider. It does not say which team, app or
   use case spent it, or why a request went to an expensive model.

Switchyard is a gateway that sits between apps and providers. It chooses a model by policy, falls back when a
provider is unhealthy, holds each team to a budget, serves repeated requests from a cache, and writes a trace for
every request that adds up exactly to a ledger.

**How it fits with Yardstick.** Switchyard governs spend and reliability. Yardstick measures quality. Switchyard
can say "this use case now routes to a cheaper tier and costs less". It cannot say whether the answers got worse.
Yardstick can. Section 9 describes how a route change could be gated by a Yardstick run.

## 2. Users

| User | What they need from Switchyard |
|---|---|
| **Platform owner** | One place to set routing and fallback policy. Evidence that an outage did not become a retry storm. |
| **Team lead with a budget** | A soft and a hard limit per period, a choice of what happens at the limit, and a warning before it. |
| **App developer** | One request shape. A clear, machine-readable reason when a request is routed, downgraded or denied. |
| **Finance reviewer** | A ledger in whole micro-dollars, append-only, where every trace record and every report total ties back to ledger entries. |

## 3. Conventions used in every section

### 3.1 Money

- The unit is the **micro-dollar** (µUSD): 1 USD = 1,000,000 µUSD. All money is a Python `int`. No float touches a
  money value anywhere: not in prices, costs, limits, ledger entries, traces or reports.
- A price is an integer number of **µUSD per 1,000,000 tokens**, one for input and one for output.
  Example: `2_000_000` means 2.000000 USD per million tokens.
- **Cost of one attempt:**

  ```text
  cost_micro = ceil((input_tokens * input_price + output_tokens * output_price) / 1_000_000)
  ```

  One ceiling, applied once to the sum, using integer arithmetic (`-(-n // 1_000_000)`). Rounding is always up, so
  the ledger never records less than the exact cost. The rounding error is under 1 µUSD per attempt.
- **Worst-case cost** of a request on a model is the same formula with `output_tokens = max_output_tokens`.
- Display: `cost_usd` is the string `micro // 1_000_000`, a dot, then `micro % 1_000_000` zero-padded to six
  digits. Example: 13200 µUSD prints as `0.013200`. A negative amount is formatted from its absolute value with a
  leading minus sign (`-0.013200`). `parse_usd` is the exact inverse.

### 3.2 Time

- All time comes from an injected clock that returns integer milliseconds since the Unix epoch, UTC. Tests use a
  fake clock that only moves when told to. No engine reads the system clock directly.
- In examples, `t=250` means 250 ms after the case's `start_at`.
- Phase 1 runs requests one at a time. A request starts at `max(clock, arrival)`. A fake provider "takes" its
  scripted latency by advancing the fake clock. Backoff advances the fake clock the same way.

### 3.3 The illustrative catalog

Fictional providers `alder`, `birch` and `cedar`. Prices are illustrative and live in a data file
(`fixtures/catalog/illustrative-catalog.yaml`, created in Phase 1), never in code.

| Model | Provider | Tier | Input price (µUSD per 1M tokens) | Output price (µUSD per 1M tokens) | Declared latency (ms) | Cleared for tags |
|---|---|---|---:|---:|---:|---|
| `alder-small` | alder | economy | 200,000 | 800,000 | 400 | `pii` |
| `birch-small` | birch | economy | 250,000 | 1,000,000 | 300 | none |
| `alder-medium` | alder | standard | 2,000,000 | 8,000,000 | 900 | `pii` |
| `birch-medium` | birch | standard | 2,500,000 | 10,000,000 | 700 | none |
| `cedar-large` | cedar | premium | 10,000,000 | 40,000,000 | 2,500 | `pii`, `restricted` |
| `alder-large` | alder | premium | 12,000,000 | 48,000,000 | 2,000 | `pii` |

Tiers are ordered `economy < standard < premium`. The known data-sensitivity tags are `pii` and `restricted`.
A fake provider's successful response takes exactly the model's declared latency unless a script says otherwise.

### 3.4 The illustrative routing policy

`fixtures/policy/illustrative-policy.yaml` (created in Phase 1). Rules are checked in file order.

| Rule id | team | app | use_case | task_class | Tiers (in order) | max_latency_ms | max_cost_micro | min_tier |
|---|---|---|---|---|---|---:|---:|---|
| `support-triage` | support | helpdesk | triage | classify | economy | 1,000 | 500 | economy |
| `support-reply` | support | `*` | reply-draft | draft | standard, premium | 3,000 | 20,000 | economy |
| `legal-review` | legal | `*` | contract-review | reason | premium | 5,000 | 200,000 | premium |
| `default` | `*` | `*` | `*` | `*` | economy, standard | 2,000 | 5,000 | economy |

### 3.5 Engine defaults used in the examples

| Setting | Value | Meaning |
|---|---:|---|
| `max_attempts` | 3 | Most attempts one request may make, across all models |
| `max_retries_per_provider` | 1 | Most retries one request may make on one provider |
| `backoff_ms` | 200 | Fixed wait before a retry. No jitter in Phase 1 |
| `breaker.window_ms` | 30,000 | How far back the breaker looks |
| `breaker.min_calls` | 4 | Fewest outcomes in the window before the breaker may open |
| `breaker.failure_rate_pct` | 50 | Opens when failures are at least this share of outcomes in the window |
| `breaker.open_ms` | 10,000 | How long the breaker stays open before a probe is allowed |
| `retry_budget.window_ms` | 10,000 | How far back the retry budget looks |
| `retry_budget.ratio_pct` | 20 | Retries allowed as a share of first attempts in the window |
| `retry_budget.min_retries` | 1 | Retries always allowed per window, however few first attempts |
| `reservation_ttl_ms` | 600,000 | Age at which `sweep` releases an unsettled hold (section 6.1) |
| `cache_max_entries` | 10,000 | Most cache entries per team (section 7.1) |

These live in the policy file's `engine` block. They are defaults for the examples, not recommendations.

### 3.6 A request

| Field | Type | Notes |
|---|---|---|
| `request_id` | string | Unique. The ledger rejects a second reserve for the same `(request_id, attempt)` |
| `team`, `app`, `use_case`, `task_class` | string | Used to match a routing rule |
| `data_tags` | set of strings | Subset of the known tags. Empty means not sensitive |
| `messages` | list of `{role, content}` | Used for the cache key. Phase 2 maps the OpenAI-compatible shape onto this |
| `input_tokens` | int ≥ 0 | Given by the fixture in Phase 1. Phase 2 counts with a deterministic fake tokenizer |
| `max_output_tokens` | int ≥ 1 | Hard cap. A fake provider never returns more |
| `temperature` | decimal string | Default `"0"` |
| `max_latency_ms`, `max_cost_micro` | optional int | May only tighten the rule's limits (the smaller value wins) |

### 3.7 Order of work for one request

0. **Validation.** A request with an unknown data tag, a missing or malformed field, or a `request_id` the
   engine has already seen ends with status `bad_request`, no attempts and `cache: not_checked`.
1. **Team lookup.** A team with no config ends the request with status `unknown_team`. There is no default team
   config.
2. **Route** (pure function, section 4). An empty chain ends the request with status `no_route`.
3. **Cache** (section 7). A bypass is recorded. A hit ends the request with status `ok` and no attempts.
4. **Attempt loop** (sections 5 and 6): breaker check, budget reserve, provider call, settle, then retry, fall
   back or stop.
5. **Cache store**, when allowed.
6. **Trace** (section 8) is written for every request, whatever its status.

Statuses: `ok`, `no_route`, `denied_budget`, `failed_exhausted`, `bad_request`, `unknown_team`.

## 4. Capability A: routing policy as data

### 4.1 Behaviour

- A policy file lists rules. Each rule has an `id`, four matchers (`team`, `app`, `use_case`, `task_class`, each
  an exact string or `*`), an ordered list of `tiers`, `max_latency_ms`, `max_cost_micro` and `min_tier`.
- **The first rule in file order whose four matchers all match wins.** The file must end with a rule whose four
  matchers are all `*`. A file without one fails to load.
- Effective limits: `max_latency_ms = min(rule, request)` and `max_cost_micro = min(rule, request)` where the
  request gives one.
- For each tier in the rule's order, each model of that tier (in model-id order) is checked in this order, and
  the **first** failing check is the model's one exclusion reason:
  1. `excluded_not_cleared`: the request's `data_tags` are not a subset of the model's cleared tags.
  2. `excluded_latency`: the model's declared latency is greater than the effective `max_latency_ms`.
  3. `excluded_cost`: the model's worst-case cost for this request is greater than the effective `max_cost_micro`.
- Models that pass are **candidates**. Within a tier they are ordered by worst-case cost, lowest first, then by
  model id. The **chain** is the candidates of the first tier, then the second, and so on. The first model in the
  chain is the planned model. The rest are the fallback order.
- `max_cost_micro` bounds the worst-case cost of **one attempt**. A request that retries or falls back can cost
  more in total. The bound on a whole request is `max_attempts * max_cost_micro`.
- Latency filtering uses the catalog's declared latency, not measured latency. The effective `max_latency_ms` is
  also the timeout for each attempt.
- The decision is **explainable**: it carries an ordered list of reasons, each `{code, subject, detail}`:
  `rule_matched` (subject: rule id), `limits_applied` (subject: rule id; detail: effective `max_latency_ms` and
  `max_cost_micro`), then one `candidate` or `excluded_*` per model considered (rule tier order, then model id;
  subject: model id; detail: `tier`, `worst_case_micro`, `declared_latency_ms`), then `chosen` (subject: the
  planned model) or `no_eligible_model` (subject: rule id).
- Routing is a pure function of `(request, policy, catalog)`. It reads no clock, health or budget state.

### 4.2 Acceptance criteria

- **AC-A1** The same `(request, policy, catalog)` always gives a byte-identical decision (canonical JSON).
- **AC-A2** Every model in the rule's tiers appears in the reasons exactly once, as `candidate` or as one
  `excluded_*`. Models in other tiers do not appear.
- **AC-A3** The chain equals the candidates in the stated order. A property test rebuilds the chain from the
  reasons alone and gets the same list.
- **AC-A4** No model in a chain lacks clearance for any of the request's tags (property test over random
  requests and catalogs).
- **AC-A5** A request-level limit can tighten a rule's limit and can never loosen it.
- **AC-A6** A policy file with no catch-all last rule, an unknown tier, an unknown tag, a duplicate rule id, a
  duplicate model id, a negative limit or a float where an integer is required fails to load with one clear error.
- **AC-A7** Worked examples WE-01 to WE-05 reproduce exactly.

### 4.3 Worked example (WE-03)

Request: team `support`, app `helpdesk`, use case `reply-draft`, task class `draft`, no tags, 3,000 input tokens,
`max_output_tokens` 1,500.

1. `rule_matched`: `support-reply`. Limits: 3,000 ms and 20,000 µUSD.
2. Standard tier: `alder-medium` worst case = (3,000 × 2,000,000 + 1,500 × 8,000,000) / 1,000,000 = 18,000. Candidate.
   `birch-medium` worst case = (3,000 × 2,500,000 + 1,500 × 10,000,000) / 1,000,000 = 22,500. `excluded_cost`.
3. Premium tier: `alder-large` worst case 108,000, `excluded_cost`. `cedar-large` worst case 90,000, `excluded_cost`.
4. `chosen`: `alder-medium`. Chain: `[alder-medium]`.

The fake returns 900 output tokens. Cost = (3,000 × 2,000,000 + 900 × 8,000,000) / 1,000,000 = 13,200 µUSD.

## 5. Capability B: fallback chains and circuit breakers

### 5.1 Attempt outcomes

A fake provider returns one of: `ok`, `timeout`, `server_error`, `rate_limited`, `bad_request`.

- `timeout`, `server_error` and `rate_limited` are **failures**. They count against provider health and may be
  retried or fallen back from.
- `bad_request` is the caller's fault. It ends the request with status `bad_request`. It is never retried, never
  falls back, and is not recorded as a health outcome.
- A scripted latency greater than the attempt timeout becomes a `timeout` after exactly the timeout.
- A failed attempt costs 0 unless its script says tokens were billed (`billed_input_tokens`,
  `billed_output_tokens`). Billed tokens are charged with the normal cost formula. A script may never bill more
  than the request's `input_tokens` and `max_output_tokens`.

### 5.2 Circuit breaker (one per provider)

States: `closed`, `open`, `half_open`. Two operations, both taking the current time:

- `allow(now)`: may this provider be called?
  - `closed`: yes.
  - `open`: no, unless `now - opened_at >= open_ms`. Then the state becomes `half_open`, the one probe permit is
    granted, and the answer is yes.
  - `half_open`: yes only if no probe is in flight (the permit is then granted). Otherwise no.
- `record(outcome, now)`: called once when an allowed attempt finishes with `ok` or a failure.
  - `closed`: add `(now, outcome)`. Drop outcomes with `ts <= now - window_ms`. If this outcome is a failure,
    there are at least `min_calls` outcomes, and `failures * 100 >= failure_rate_pct * outcomes`, the state
    becomes `open` with `opened_at = now`.
  - `half_open`: the probe is over. `ok` closes the breaker and clears its outcomes. A failure reopens it with
    `opened_at = now`.
  - `open`: can only happen when two calls were allowed at once and the first one opened the breaker (Phase 2).
    The outcome is added to the window and nothing else changes.
  - A failed probe is not added to the outcome window. The window is cleared only when the breaker closes.
- A probe that ends in `bad_request`, or whose budget reserve is refused before the call, gives the permit back
  without changing state.
- Every state change is a trace event on the request that caused it: `probe` (open to half-open, permit
  granted), `breaker_opened`, `breaker_closed`, each with the provider and the time.

A provider whose breaker says no is **skipped**: no attempt, no reserve, no charge. The skip is a trace event
(`breaker_open_skip`, one event per skipped model). All models of a skipped provider are skipped.

### 5.3 Retry, fall back or stop

After a failed attempt on model M of provider P, the engine checks in this order. The first check that fails is
the recorded reason for not retrying.

1. `max_attempts`: the request has made fewer than `max_attempts` attempts.
2. `per_request_retry_limit`: the request has made fewer than `max_retries_per_provider` retries on P.
3. `breaker_open`: P's breaker is still `closed`.
4. `retry_budget_exhausted`: P's retry budget allows one more retry.

If all four pass, the engine waits `backoff_ms` and retries M. Otherwise it moves to the next model in the chain
whose provider's breaker allows a call. If the chain is used up or `max_attempts` is reached, the request ends
with status `failed_exhausted`.

**Retry budget (one per provider).** A **retry** is an attempt on the same model straight after a failed attempt
on it by the same request. A **first attempt** is a request's first attempt on P (probes included). Any other
attempt on P counts as neither. The budget keeps the start times of both. After a failure at time `now`, a retry
is allowed when

```text
retries_in_window < max(min_retries, floor(first_attempts_in_window * ratio_pct / 100))
```

where "in window" means `ts > now - window_ms`. Falling back to another provider is not a retry and does not
draw on the budget. This is what stops a retry storm: once a provider is failing, almost every request makes one
attempt on it and moves on, and once its breaker opens, none do.

`fallback_used` is set on a trace when the request was served and, earlier in the same request, a **different**
model was attempted or was skipped because of its breaker. A retry on the same model does not set it. A budget
downgrade on its own sets `downgraded`, not `fallback_used`.

### 5.4 Acceptance criteria

- **AC-B1** Deterministic: the same case run twice under the fake clock gives byte-identical traces, breaker
  states and ledger.
- **AC-B2** No attempt starts on a provider while its breaker is `open` and `now - opened_at < open_ms`.
- **AC-B3** In `half_open`, at most one probe is in flight (property test over random allow/record sequences).
- **AC-B4** No request makes more than `max_attempts` attempts or more than `max_retries_per_provider` retries
  on one provider.
- **AC-B5** At the moment each retry is allowed (the failure time), the retries already in the window are fewer
  than `max(min_retries, floor(first_attempts_in_window * ratio_pct / 100))`.
- **AC-B6** `bad_request` is never retried, never falls back and never changes breaker state.
- **AC-B7** A skipped provider produces no ledger entry.
- **AC-B8** Worked examples WE-06 to WE-13 reproduce exactly, including every timestamp.

### 5.5 Worked example (the provider-outage case, WE-06 to WE-11)

`alder` returns `server_error` after 50 ms for every call that starts in `[0, 20000)`. Team `support` sends the
same triage request six times (400 input tokens, `max_output_tokens` 50, fake output 12 tokens, cache off).
Chain: `[alder-small, birch-small]`. Worst case: 120 on `alder-small`, 150 on `birch-small`. Actual: 90 and 112.

| Request | Starts | What happens | Charged |
|---|---:|---|---:|
| WE-06 | t=0 | `alder-small` fails at t=50. Retry allowed (retry budget: 0 used, 1 allowed). Backoff to t=250. Fails at t=300. No second retry: `per_request_retry_limit`. `birch-small` t=300 to t=600, ok | 112 |
| WE-07 | t=1000 | `alder-small` fails at t=1050. No retry: `retry_budget_exhausted` (2 first attempts in window, 1 retry allowed, 1 used). `birch-small` to t=1350, ok | 112 |
| WE-08 | t=2000 | `alder-small` fails at t=2050. That is 4 outcomes, 4 failures: breaker **opens** at t=2050. No retry: `breaker_open`. `birch-small` to t=2350, ok | 112 |
| WE-09 | t=3000 | `alder-small` skipped (`breaker_open_skip`), no attempt. `birch-small` to t=3300, ok | 112 |
| WE-10 | t=13000 | 13000 − 2050 ≥ 10000: `half_open`, probe granted. `alder-small` fails at t=13050: breaker reopens at t=13050. `birch-small` to t=13350, ok | 112 |
| WE-11 | t=24000 | 24000 − 13050 ≥ 10000: probe granted. The outage is over. `alder-small` ok at t=24400: breaker **closes** | 90 |

Totals: 6 requests, 11 attempts (5 failed), 650 µUSD charged. If each of the five requests sent during the
outage had tried `alder` and retried once, they would have made 10 calls to the failing provider. Here they made
5, and none between t=2050 and t=13000.

## 6. Capability C: per-team budgets

### 6.1 Ledger

An append-only list of entries. Nothing is edited or deleted. A correction is a new entry.

| Field | Notes |
|---|---|
| `seq` | 1, 2, 3 … with no gaps |
| `ts_ms` | From the injected clock |
| `team`, `period` | Period is a UTC calendar month, `YYYY-MM` |
| `kind` | `reserve`, `settle`, `release`, `adjustment` |
| `request_id`, `attempt` | Which attempt (1-based). Empty for an adjustment |
| `amount_micro` | `reserve`: amount held. `settle`: actual cost. `release`: 0. `adjustment`: signed amount |
| `ref_seq` | `settle` and `release`: the `seq` of the reserve they close |
| `flags` | `soft_limit_crossed`, `over_hard_limit` (on a reserve); `unsettled` (on a release made by `sweep`) |
| `memo` | Required for `adjustment` and `release` |

For one team and period:

```text
spent     = sum(settle.amount) + sum(adjustment.amount)
held      = sum(reserve.amount for reserves not yet closed by a settle or release)
committed = spent + held
```

- A `settle` or `release` is posted to the **period of the reserve it closes**, whatever the clock says when it
  settles. The **accounting time** of an attempt is its reserve time. Daily reports use the same rule: a settle
  is counted on the UTC day of the reserve its `ref_seq` points at, never on the day of its own `ts_ms`.
- `settle.amount` may not exceed the reserve it closes. Every reserve is closed exactly once.
- `release` closes a reserve with no spend. Phase 1 uses it only in `sweep(now)`, which releases reserves older
  than `reservation_ttl_ms` and writes each release with the flag `unsettled` for a person to look at. If the
  provider did bill, the fix is a manual `adjustment`.
- `adjustment` is a manual correction or an opening balance. It is posted to the period of its own `ts_ms`. It is
  not checked against limits. It counts toward `spent`, and so toward `committed` in every later reserve check.
  It may not take `spent` below 0. Fixtures use it to seed "already spent this period".

### 6.2 Reserve, then settle

Each **attempt** is reserved and settled on its own:

1. Before the call: `reserve(worst-case cost of this attempt's model)`.
2. After the call: `settle(actual cost)`. A failed attempt that billed nothing settles 0.

Team config (illustrative numbers appear in the examples):

```yaml
teams:
  support:
    budget:
      period: calendar_month_utc
      soft_limit_micro: 8000
      hard_limit_micro: 10000
      overage: deny            # deny | downgrade | allow_with_flag
      overage_cap_micro: 0     # required and > 0 only for allow_with_flag
    cache: {enabled: true, ttl_ms: 60000}
```

`reserve(amount)` at time `now`, for the period containing `now`, under one lock:

```text
ceiling = hard_limit + (overage_cap if overage == allow_with_flag else 0)
if committed + amount > ceiling: refuse
otherwise append the reserve, with flags:
  soft_limit_crossed  if committed + amount > soft_limit
  over_hard_limit     if committed + amount > hard_limit
```

Both flags are about the **hold**: they describe `committed` at reserve time, counting this reserve at its
worst-case amount. A request can carry `soft_limit_crossed` and finish with `spent` still under the soft limit
(WE-14), and every reserve above the soft limit carries the flag, not only the first. Reports label the counts
"reserves above the soft limit".

A reserve that lands exactly on the ceiling is allowed (`<=`). Because the check and the append happen under one
lock and count `held`, two requests in flight cannot both take the last of a budget.

### 6.3 What happens when a reserve is refused

| `overage` | Behaviour |
|---|---|
| `deny` | The request ends with status `denied_budget`. Charges from its earlier attempts stand |
| `downgrade` | Once per request, the rest of the chain is replaced by the **downgrade chain**: every eligible model (same clearance, latency and cost checks as routing) in tiers strictly below the refused model's tier and not below the rule's `min_tier`, highest tier first, cheapest first within a tier. The engine tries to reserve each in order. A model whose reserve is also refused is skipped (`budget_refused`). If none can be reserved, status `denied_budget`. A served request carries the flag `downgraded` |
| `allow_with_flag` | The ceiling is `hard_limit + overage_cap`. Reserves above the hard limit carry `over_hard_limit`. A reserve above the ceiling ends the request with `denied_budget` |

Details that apply to every policy:

- A refused reserve and a breaker skip do not use an attempt number and do not count toward `max_attempts`.
- "Below" always means the global tier order (`economy < standard < premium`), whatever order the rule lists.
- The downgrade chain leaves out any model this request has already attempted.
- Inside the downgrade chain the normal breaker, retry and fallback rules apply.

A refused reserve writes no ledger entry. It is a trace event (`budget_refused`, with the amount and the
balances at that moment). Soft limits never block. They flag.

Period rollover: a new period starts with `spent = 0` and `held = 0`. Unused budget does not carry over.
Limits are read from team config at reserve time.

### 6.4 Acceptance criteria

- **AC-C1** Every accepted reserve leaves `committed <= hard_limit + overage_cap` (cap is 0 unless
  `allow_with_flag`), with adjustments included in `committed`. A settle or release never raises `committed`. So
  only an adjustment can take `committed` above the ceiling. Property test over random interleavings of reserve,
  settle and adjustment steps from many requests.
- **AC-C2** With `deny` or `downgrade`, in a period where no adjustment is posted after the first reserve,
  `spent <= hard_limit` at every prefix of the ledger.
- **AC-C3** A threaded test (32 threads, each reserving against a budget that fits 10) ends with exactly 10
  reserves accepted and no overspend, on every one of 50 runs.
- **AC-C4** `settle > reserve`, a second settle, a settle with no reserve, and a duplicate
  `(request_id, attempt)` reserve all raise `LedgerError` and append nothing.
- **AC-C5** `seq` has no gaps. No public method changes or removes an entry. A ledger written to JSON Lines and
  read back gives the same balances.
- **AC-C6** A settle after midnight UTC on the last day of a month posts to the month of its reserve.
- **AC-C7** Money fields reject `float`, `bool` and numeric strings at the model boundary.
- **AC-C8** Worked examples WE-14 to WE-23 reproduce exactly.

### 6.5 Worked example (concurrent overspend, WE-17)

Team `support`: hard limit 10,000, soft limit 8,000, `deny`. Opening balance 9,790 (an `adjustment`). Three
triage requests A, B and C, each reserving 120 and costing 90. The steps interleave:

| Step | Operation | committed before | Check | Result | spent | held |
|---:|---|---:|---|---|---:|---:|
| 1 | reserve A, 120 | 9,790 | 9,910 ≤ 10,000 | accepted, `soft_limit_crossed` | 9,790 | 120 |
| 2 | reserve B, 120 | 9,910 | 10,030 > 10,000 | **refused**: B ends `denied_budget` | 9,790 | 120 |
| 3 | settle A, 90 | | | hold of 120 released, 90 spent | 9,880 | 0 |
| 4 | reserve C, 120 | 9,880 | 10,000 ≤ 10,000 | accepted (exactly at the limit), `soft_limit_crossed` | 9,880 | 120 |
| 5 | settle C, 90 | | | | 9,970 | 0 |

Without the hold, A and B would both have seen 9,790 + 120 ≤ 10,000 and both run. Here B is refused while A is
in flight. B's refusal is conservative: A went on to cost 90, not 120. That is the price of never overspending.

## 7. Capability D: caching

### 7.1 Behaviour

- **Exact match only.** The key is `sha256` of the canonical JSON of:

  ```text
  {v: 1, team, rule_id, use_case, task_class, messages (normalized), input_tokens, max_output_tokens,
   temperature, max_latency_ms (effective), max_cost_micro (effective), config_version}
  ```

  The lookup happens after routing, so the matched rule id and the effective limits are known. With them in the
  key, a hit can only return an answer made for a request with the same rule and the same chain.
  `config_version` is the content hash of the routing policy plus the catalog, so any policy or catalog change
  empties the cache in effect. `app`, `request_id` and timestamps are not in the key.
- **Normalization** of each message: role lower-cased; content converted to Unicode NFC, `\r\n` and `\r` turned
  into `\n`, leading and trailing whitespace removed. Nothing else changes: no case folding, no collapsing of
  inner whitespace.
- **Scope is one team.** Teams never share entries.
- **Bypass** (no read and no write), first match wins:
  1. `bypass_sensitive`: the request has any data tag.
  2. `bypass_team_opt_out`: the team's `cache.enabled` is false.
  3. `bypass_nondeterministic`: `temperature` is not `"0"`.
- **TTL.** An entry stored at `stored_at` is valid while `now - stored_at < ttl_ms`. At exactly `ttl_ms` it is
  expired. `stored_at` is the time the attempt finished.
- **Hit.** Status `ok`, no attempts, cost 0, no ledger entry, no budget check. The trace records
  `cache: hit` and `avoided_micro`, the cost of the attempt that produced the entry. A hit is served even when
  the team is at its hard limit, because it costs nothing.
- **Store.** After an `ok` attempt, when the request was not bypassed, was not `downgraded`, and the serving
  model's tier equals the first tier of the rule. This stops a cheaper answer, given under budget or outage
  pressure, from being replayed later as the normal answer.
- Size: `cache_max_entries` per team (section 3.5). When full, the entry with the oldest `stored_at` is dropped
  (ties by key).
- **Semantic caching** (matching on meaning rather than exact text) is a later phase. It is not built, and
  nothing in Phases 1 to 4 depends on it.

### 7.2 Acceptance criteria

- **AC-D1** A request with any data tag never reads or writes the cache (property test: after any sequence of
  requests, no stored entry came from a tagged request, and no tagged request has `cache: hit`).
- **AC-D2** Two requests that differ only in `request_id`, `app` (when the same rule still matches), line
  endings, Unicode composition, or leading and trailing whitespace of a message have the same key. Two that differ in any other key field do not.
- **AC-D3** A hit appends nothing to the ledger and has `cost_micro` 0.
- **AC-D4** An entry is a hit at `ttl_ms - 1` and a miss at `ttl_ms`.
- **AC-D5** A team with `cache.enabled: false` never reads or writes.
- **AC-D6** A `downgraded` response, or one served from any tier other than the rule's first tier, is never
  stored.
- **AC-D7** Worked examples WE-24 to WE-29 reproduce exactly.

### 7.3 Worked example (WE-24 to WE-27)

Team `research` (cache on, TTL 60,000 ms) sends the same request under the `default` rule: 1,000 input tokens,
`max_output_tokens` 200, fake output 150, temperature `"0"`. On `alder-small` the cost is
(1,000 × 200,000 + 150 × 800,000) / 1,000,000 = 320.

| Request | Starts | Cache | Charged | Note |
|---|---:|---|---:|---|
| WE-24 | t=0 | miss, then stored at t=400 | 320 | |
| WE-25 | t=5000 | hit | 0 | Content has two trailing spaces. Same key. `avoided_micro` 320 |
| WE-26 | t=60399 | hit | 0 | Age 59,999 < 60,000 |
| WE-27 | t=60400 | miss, stored again at t=60800 | 320 | Age 60,000 is not < 60,000 |

## 8. Capability E: cost tracing and the daily report

### 8.1 Trace record (one per request, JSON Lines)

| Field | Notes |
|---|---|
| `request_id`, `team`, `app`, `use_case`, `task_class`, `data_tags` | From the request |
| `arrived_ms`, `finished_ms` | From the injected clock |
| `config_version` | Hash of policy plus catalog |
| `route` | Rule id, chain, ordered reasons (section 4) |
| `cache_key` | The key hash, or empty when the cache was not checked or was bypassed |
| `cache` | `hit`, `miss`, one of the three bypass codes, or `not_checked` when the request ended before the cache step. `avoided_micro` on a hit |
| `events` | Ordered runtime events, each `{code, at_ms, subject, detail}`: `breaker_open_skip`, `probe`, `breaker_opened`, `breaker_closed`, `retry`, `no_retry` (detail: reason), `budget_refused` (detail: amount, `spent`, `held`, ceiling), `downgrade_chain` (detail: the models) |
| `attempts` | Ordered. Each: `attempt`, `provider`, `model`, `started_ms`, `finished_ms`, `outcome`, `input_tokens`, `output_tokens` (billed), `reserved_micro`, `cost_micro`, `period`, `reserve_seq`, `settle_seq` |
| `status` | One of the six statuses |
| `flags` | `soft_limit_crossed`, `over_hard_limit`, `downgraded`, `fallback_used` |
| `cost_micro` | Sum of `attempts[].cost_micro` |

The message text is **not** in the trace. Only the cache key hash is.

### 8.2 Reconciliation

The trace adds up exactly to the ledger:

- Per attempt: `cost_micro` equals the `settle` entry at `settle_seq`, and `reserved_micro` equals the `reserve`
  entry at `reserve_seq`.
- Per request: `cost_micro` equals the sum of settles carrying its `request_id`.
- Per team and period, and per team and UTC day: the sum of attempt costs (by accounting time) equals the sum of
  settles. `spent` equals that sum plus adjustments.
- After a run with nothing in flight, `held` is 0 for every team and period.

`reconcile` also checks the ledger itself: `seq` runs 1 to n with no gap, every reserve is closed exactly once,
every `ref_seq` points at a reserve with the same `(request_id, attempt)`, and each attempt's settle has
`ref_seq == reserve_seq`.

`reconcile(traces, ledger)` returns a list of differences. An empty list is the only passing result.

### 8.3 Daily per-team cost report

Markdown and JSON, one row per team and UTC day:

`date`, `team`, `requests` (by arrival day), counts per status, `cache_hits`, `attempts`, `failed_attempts`,
`input_tokens` and `output_tokens` (billed), `cost_micro`, `cost_usd`, `avoided_micro` (labelled "estimate, not in
the ledger"), `adjustments_micro`, flag counts, then period-to-date `spent`, `soft_limit`, `hard_limit` and
`remaining` (`hard_limit - spent`, which may be negative under `allow_with_flag`).

Which day each column belongs to:

- **By arrival day of the request:** `requests`, the status counts, `cache_hits`, the flag counts and
  `avoided_micro`.
- **By reserve day of the attempt:** `attempts`, `failed_attempts`, `input_tokens`, `output_tokens` and the cost
  columns.
- **By the day of its own `ts_ms`:** `adjustments_micro`.
- **Period-to-date** columns are for the period containing the row's day, as of the end of that day by
  accounting time (settles by reserve day, adjustments by their own day).
- A row exists for every team and day with at least one arrival, one reserve or one adjustment.
- Limits come from team config. A team with no config (`unknown_team` requests) shows blank limits.

So a request that arrives before midnight and makes an attempt after it is counted as a request on the first
day and as cost on the day of each attempt. The report says so in a footnote.

The report runs `reconcile` first. If there is any difference it prints the differences and exits 1 instead of
printing totals.

### 8.4 Acceptance criteria

- **AC-E1** Every request produces exactly one trace record, whatever its status.
- **AC-E2** `reconcile` returns no differences for every golden case and for hypothesis-generated runs.
- **AC-E3** Changing any one `cost_micro` in a trace, or removing any one `reserve` or `settle` line from the
  ledger, makes `reconcile` report a difference. (A removed trailing adjustment cannot be detected from traces.
  That would need a control total or hash chain, which is not in scope.)
- **AC-E4** The sum of a team's daily `cost_micro` over a period plus its adjustments equals the ledger's `spent`
  for that period.
- **AC-E5** No trace contains message text.
- **AC-E6** Worked example WE-30 reproduces exactly.

### 8.5 Worked example (WE-30)

The daily report row for team `support` after the provider-outage case (section 5.5):

| requests | ok | cache_hits | attempts | failed_attempts | input_tokens | output_tokens | cost_micro | cost_usd |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 6 | 6 | 0 | 11 | 5 | 2,400 | 72 | 650 | 0.000650 |

Ledger for the case: 22 entries. 11 reserves totalling 1,470 (six of 120 and five of 150). 11 settles totalling
650 (five of 112, one of 90, five of 0). `held` ends at 0. 650 = 5 × 112 + 90.

## 9. Capability F: hand-off to Yardstick (design only, not built)

A **route change** is a change to the policy file that moves a use case to a different tier or model. It can cut
cost and quietly cut quality. Switchyard cannot see quality. The gate below is a design. No part of it is built
in Phases 1 to 4, and it has not been run.

1. A rule may name an eval suite: `eval: {suite: evals/reply-draft.yaml, gate: evals/reply-draft-gate.yaml}`.
   These are Yardstick suite and gate files kept in the repo that owns the use case.
2. A pull request changes the policy file. A CI step runs `switchyard policy diff base.yaml head.yaml` (a Phase 3
   command) and prints, per rule, the planned model before and after for that rule's eval suite inputs.
3. For each changed rule with an `eval` block, CI runs the suite through Switchyard with the **new** policy,
   using Yardstick's `http` target against the Phase 2 gateway on `127.0.0.1`:
   `yardstick run -s <suite> --http <target.yaml> --target-name <rule id> -o runs/<rule id>.json`.
   The gateway's response carries `latency_ms` and `cost_usd` (the trace's `cost_micro` as a six-decimal string),
   which Yardstick's `http` target reads.
4. `yardstick gate runs/<rule id>.json -c <gate file>` compares against the baseline pinned for the **old**
   route. Exit 0 passes, 1 fails the check, 2 means bad input. A gate file can hold a quality floor and
   `max_drop`, and also `max` or `max_rise` on `latency_p95_ms` and `cost_per_item_usd`.
5. On merge, `yardstick baseline pin` records the new run as the baseline.

The same idea applies to budget downgrades: a rule's `min_tier` should only be set below its first tier when a
Yardstick baseline exists for the use case on the lower tier.

**What this does not prove here.** Switchyard's providers are fakes that return canned text. A Yardstick run over
them measures the wiring, cost and latency numbers, not answer quality. A real quality gate needs real models,
which this project does not call.

## 10. Invariants (`tests/invariants/`)

| ID | Invariant |
|---|---|
| SW-01 | **Deterministic.** Same catalog, policy, team config, scripts, requests and fake clock give byte-identical traces, ledger and report |
| SW-02 | **No overspend.** Every accepted reserve leaves `spent + held <= hard_limit + overage_cap`, and nothing but an adjustment can raise `spent + held` past it (AC-C1, AC-C2) |
| SW-03 | **Ledger integrity.** Append-only, gapless `seq`, every reserve closed exactly once, `settle <= reserve` |
| SW-04 | **Reconciliation.** Traces add up exactly to the ledger, and the report to both (section 8.2) |
| SW-05 | **Sensitive data.** A tagged request never reaches a model not cleared for its tags and never touches the cache |
| SW-06 | **No retry storm.** Breaker and retry-budget bounds hold (AC-B2 to AC-B5) |
| SW-07 | **Integer money.** No float in any money path |
| SW-08 | **Explainable.** Every decision's reasons are complete and sufficient to rebuild its chain (AC-A2, AC-A3) |
| SW-09 | **Fakes only.** The package imports no network client and reads no environment variable. A test scans `src/` for `os.environ`, `getenv`, `socket`, `http`, `urllib`, `requests`, `httpx` and known provider SDK names. From Phase 2 the one `server` package is exempt for its local HTTP framework only |

## 11. Worked examples that Phase 1 tests must reproduce

All numbers are illustrative. Money is µUSD. Each row is asserted by a golden test (`fixtures/README.md` gives
the case files). Each row gives the request's **complete** set of flags: a row that names none has none. Unless a row says otherwise: budgets are far from their limits, and the serving model's success
takes its declared latency. The team's cache is **off** in every case except `c08`, `c13` and `c14`, so repeated
requests in the other cases are never served from cache. "res" is the amount reserved and "cost" the amount settled.

| ID | Case file | Input | Expected route (chain) | Expected attempts | Expected charges and end state |
|---|---|---|---|---|---|
| WE-01 | `c01-routing-basics` | support / helpdesk / triage / classify, 400 in, max out 50, fake out 12 | rule `support-triage`; chain `[alder-small, birch-small]` (worst case 120, 150) | 1: `alder-small` ok | res 120, cost 90 |
| WE-02 | `c01-routing-basics` | marketing / site / blurb / summarize, tag `pii`, 1,000 in, max out 200, fake out 150 | rule `default`; `birch-small` and `birch-medium` `excluded_not_cleared`; chain `[alder-small, alder-medium]` | 1: `alder-small` ok | res 360, cost 320; cache `bypass_sensitive` |
| WE-03 | `c01-routing-basics` | support / helpdesk / reply-draft / draft, 3,000 in, max out 1,500, fake out 900 | rule `support-reply`; `birch-medium` (22,500), `alder-large` (108,000), `cedar-large` (90,000) `excluded_cost`; chain `[alder-medium]` | 1: `alder-medium` ok | res 18,000, cost 13,200 |
| WE-04 | `c01-routing-basics` | legal / clm / contract-review / reason, tag `restricted`, request `max_latency_ms` 2,000, 20,000 in, max out 2,000 | rule `legal-review`; `alder-large` `excluded_not_cleared`; `cedar-large` `excluded_latency` (2,500 > 2,000); chain `[]` | none | status `no_route`; no ledger entry; cost 0 |
| WE-05 | `c01-routing-basics` | marketing / site / blurb / summarize, 1,234 in, max out 100, fake out 57 | rule `default`; chain `[alder-small, birch-small, alder-medium, birch-medium]` | 1: `alder-small` ok | res 327 (exact 326.8), cost 293 (exact 292.4): the ceiling rule |
| WE-06 | `c02-provider-outage` | triage request as WE-01, t=0; `alder` fails `[0, 20000)` | chain `[alder-small, birch-small]` | 3: `alder-small` server_error 0–50; retry 250–300 server_error; `birch-small` 300–600 ok | res 120, 120, 150; cost 0, 0, 112; flags `fallback_used` |
| WE-07 | `c02-provider-outage` | same, t=1000 | same | 2: `alder-small` server_error 1000–1050; no retry (`retry_budget_exhausted`); `birch-small` 1050–1350 ok | cost 0, 112; flags `fallback_used` |
| WE-08 | `c02-provider-outage` | same, t=2000 | same | 2: `alder-small` server_error 2000–2050, breaker opens; `birch-small` 2050–2350 ok | cost 0, 112; flags `fallback_used`; `alder` breaker `open` at 2050 |
| WE-09 | `c02-provider-outage` | same, t=3000 | same | 1: `alder-small` skipped (`breaker_open_skip`); `birch-small` 3000–3300 ok | res 150, cost 112; flags `fallback_used`; no ledger entry for `alder` |
| WE-10 | `c02-provider-outage` | same, t=13000 | same | 2: probe `alder-small` server_error 13000–13050, breaker reopens; no retry (`breaker_open`); `birch-small` 13050–13350 ok | cost 0, 112; flags `fallback_used` |
| WE-11 | `c02-provider-outage` | same, t=24000 (outage over) | same | 1: probe `alder-small` ok 24000–24400, breaker closes | res 120, cost 90; case total 650 |
| WE-12 | `c03-attempt-errors` | marketing / site / blurb / summarize, 1,000 in, max out 200, fake out 150, t=0; `alder` scripted latency 9,999 ms for calls starting in `[0, 2100)`, billing 1,000 in and 80 out | rule `default`; chain `[alder-small, birch-small, alder-medium, birch-medium]` | 2: `alder-small` timeout 0–2000 (timeout = 2,000); backoff; `alder-small` 2200–2600 ok | res 360, 360; cost 264, 320; request total 584 |
| WE-13 | `c03-attempt-errors` | same request, t=10000; `alder` returns `bad_request` after 30 ms for calls starting in `[10000, 10100)` | same chain | 1: `alder-small` bad_request 10000–10030; no retry, no fallback | res 360, cost 0; status `bad_request`; breaker unchanged |
| WE-14 | `c04-budget-soft-limit` | triage request; support soft 8,000, hard 10,000, `deny`, opening 7,900 | chain `[alder-small, birch-small]` | 1: `alder-small` ok | 7,900 + 120 = 8,020 > 8,000: `soft_limit_crossed`; cost 90; spent 7,990 |
| WE-15 | `c05-budget-hard-deny` | triage request; same limits, opening 9,950 | same | none | 9,950 + 120 = 10,070 > 10,000: refused; status `denied_budget`; spent 9,950; no new ledger entry |
| WE-16 | `c06-budget-boundary` | triage request; same limits, opening 9,880 | same | 1: `alder-small` ok | 9,880 + 120 = 10,000 ≤ 10,000: accepted; flags `soft_limit_crossed`; cost 90; spent 9,970 |
| WE-17 | `c07-concurrent-overspend` | three triage requests A, B, C interleaved; same limits, opening 9,790 | same | A ok; B none; C ok | A reserved (9,910, `soft_limit_crossed`); B refused (10,030); A settles 90 (spent 9,880); C reserved (10,000, `soft_limit_crossed`); C settles 90; spent 9,970, held 0 |
| WE-18 | `c08-budget-downgrade` | support / helpdesk / reply-draft / draft, 800 in, max out 400, fake out 350; soft 8,000, hard 10,000, `downgrade`, opening 9,000 | rule `support-reply`; chain `[alder-medium, birch-medium]` (4,800, 6,000); premium `excluded_cost` (28,800, 24,000) | 1: `alder-medium` reserve 4,800 refused (13,800 > 10,000); downgrade chain `[alder-small, birch-small]` (480, 600); `alder-small` ok | res 480, cost 440; `downgraded`, `soft_limit_crossed`; spent 9,440; cache `miss` and nothing stored (0 entries) |
| WE-19 | `c09-budget-downgrade-denied` | as WE-18, opening 9,700 | same | none | 4,800 refused; 480 refused (10,180); 600 refused (10,300); status `denied_budget`; spent 9,700 |
| WE-20 | `c10-budget-allow-flag` | research / nb / notes / summarize, 1,000 in, max out 200, fake out 150, t=0; soft 8,000, hard 10,000, `allow_with_flag`, cap 2,000, opening 9,950 | rule `default`; chain `[alder-small, birch-small, alder-medium, birch-medium]` | 1: `alder-small` ok | 9,950 + 360 = 10,310: over hard, under ceiling 12,000; `over_hard_limit`, `soft_limit_crossed`; cost 320; spent 10,270 |
| WE-21 | `c10-budget-allow-flag` | same team, t=1000, 4,000 in, max out 1,200 | rule `default`; `alder-medium` (17,600) and `birch-medium` (22,000) `excluded_cost`; chain `[alder-small, birch-small]` (1,760, 2,200) | none | 10,270 + 1,760 = 12,030 > 12,000: refused; status `denied_budget`; spent 10,270 |
| WE-22 | `c11-period-rollover` | triage request twice; support soft 8,000, hard 10,000, `deny`; 2026-11 opening 9,950; first at 2026-11-30T23:59:59.000Z, second at 2026-12-01T00:00:00.000Z | chain `[alder-small, birch-small]` | first: none; second: 1 `alder-small` ok | first refused (period 2026-11); second res 120, cost 90 in period 2026-12, no flags; 2026-11 spent 9,950; 2026-12 spent 90 |
| WE-23 | `c12-period-straddle` | triage request at 2026-11-30T23:59:59.900Z; support soft 8,000, hard 10,000, `deny`; 2026-11 opening 9,000 | same | 1: `alder-small` ok, finishes 2026-12-01T00:00:00.300Z | reserve and settle both post to 2026-11; flags `soft_limit_crossed` (9,120 > 8,000); 2026-11 spent 9,090; 2026-12 has no entries; daily report counts the 90 on 2026-11-30 |
| WE-24 | `c13-cache-exact` | research / nb / notes / summarize, 1,000 in, max out 200, fake out 150, temp `"0"`, TTL 60,000, t=0 | rule `default`; chain as WE-20 | 1: `alder-small` ok 0–400 | cache `miss`; cost 320; stored at t=400 |
| WE-25 | `c13-cache-exact` | same, two trailing spaces in content, new `request_id`, t=5000 | same | none | cache `hit`; cost 0; `avoided_micro` 320; no ledger entry |
| WE-26 | `c13-cache-exact` | same as WE-24, t=60399 | same | none | cache `hit` (age 59,999) |
| WE-27 | `c13-cache-exact` | same as WE-24, t=60400 | same | 1: `alder-small` ok 60400–60800 | cache `miss` (age 60,000); cost 320; case spent 640 |
| WE-28 | `c14-cache-sensitive` | the WE-02 request at t=0 and t=5000 (cache on for the team) | chain `[alder-small, alder-medium]` | 1 each: `alder-small` ok | both `bypass_sensitive`; cost 320 + 320 = 640; cache holds 0 entries |
| WE-29 | `c15-cache-opt-out` | legal / clm / contract-review / reason, 2,000 in, max out 500, fake out 400, at t=0 and t=5000; legal `cache.enabled: false` | rule `legal-review`; chain `[cedar-large, alder-large]` (40,000, 48,000) | 1 each: `cedar-large` ok | both `bypass_team_opt_out`; cost 36,000 + 36,000 = 72,000 |
| WE-30 | `c02-provider-outage` | daily report for `support` after WE-06 to WE-11 | n/a | 11 attempts, 5 failed | 6 requests, cost 650, `cost_usd` `0.000650`; 22 ledger entries; reserves total 1,470; settles total 650; held 0 |

Thirty worked examples. WE-17 is the concurrent-overspend case. WE-06 to WE-11 are the provider-outage case.

## 12. Non-goals

- Real provider calls, API keys, or any claim about a real vendor's price, latency or quality.
- Deployment, hosting, or any statement that Switchyard is live, launched or in production.
- Semantic caching. Streaming responses. Tool calls, images or audio.
- Authentication and tenant isolation beyond a team name on the request. The Phase 2 gateway trusts a header and
  binds to `127.0.0.1` only.
- A database. Phases 1 to 4 keep the ledger and traces in memory and in JSON Lines files.
- Multi-process or multi-host correctness. The ledger lock is one process. Section 13 lists what a shared store
  would need.
- Invoicing, chargeback, tax or currency conversion. The ledger is in USD micro-dollars only.
- Measuring answer quality. That is Yardstick's job.
- Load or latency benchmarks. No performance number is claimed.

## 13. Open questions

Each has a default that Phase 1 builds. Changing one later is a `DECISIONS.md` row and a test change.

| # | Question | Default | Why |
|---|---|---|---|
| Q1 | Reserve per attempt at worst-case cost, or once per request? | Per attempt | Holds are tight and close quickly. A per-request hold would have to cover every retry and fallback, so it would block far more of the budget than is ever spent |
| Q2 | What if a real provider bills more than the reserve (its token count differs from ours)? | Not possible with fakes: `input_tokens` is exact and output is capped. `settle > reserve` raises | A real adapter would need an explicit rule (reserve a margin, or allow a flagged over-settle). Out of scope until real providers exist |
| Q3 | Budget period and time zone | Calendar month, UTC, no carry-over of unused budget | Simple to audit. A team in another time zone sees rollover at a fixed local hour |
| Q4 | Should `allow_with_flag` be unbounded? | No. It requires `overage_cap_micro` | "Allow" with no ceiling is not a budget |
| Q5 | Cache only `temperature "0"`? | Yes | A caller asking for variety should not get the same answer back |
| Q6 | Cache scope: per team or shared | Per team | A shared cache lets one team learn that another asked the same thing |
| Q7 | Breaker per provider or per model | Per provider | Outages are usually provider-wide. Per-model state learns more slowly |
| Q8 | Backoff jitter | None in Phase 1 | Keeps examples readable. Phase 2 can add jitter from a seeded generator without losing determinism |
| Q9 | A deadline for the whole request | None. `max_attempts` and the per-attempt timeout bound it | Worst case is `max_attempts * max_latency_ms + (max_attempts - 1) * backoff_ms`. A deadline is a Phase 2 question |
| Q10 | Latency filtering on declared or measured latency | Declared | Keeps routing a pure function. Measured latency is a breaker concern |
| Q11 | Stale reserves after a crash | `sweep(now)` releases reserves older than `reservation_ttl_ms` with spend 0 and marks them `unsettled` | Releasing frees the budget. The mark tells a person to check whether the provider billed. It can under-count spend, so it is never silent |
| Q12 | Open-state duration after repeated failed probes | Constant `open_ms` | Simple and deterministic. Growing it is a later option |
| Q13 | What a shared ledger store would need (multi-process) | Not built | An atomic "check committed and append" in one transaction, and a unique key on `(request_id, attempt, kind)` |

## 14. Phase plan

| Phase | Scope | Exit gate |
|---|---|---|
| **1. Pure engines** | Routing, breaker and retry budget, ledger and budgets, exact cache, fake providers, engine, trace, reconcile, daily report, a small CLI to replay a case. Pure, deterministic code. **No HTTP server.** | ruff, mypy and pytest green in CI; SW-01 to SW-09 green; all 30 worked examples reproduced by golden tests |
| **2. HTTP gateway** | An HTTP server with an OpenAI-compatible request shape (`POST /v1/chat/completions`), bound to `127.0.0.1`, in front of the Phase 1 engine and the fakes. Team, app and use case from headers. JSON Lines ledger and traces on disk. | The golden cases replayed over HTTP give the same traces and ledger as Phase 1; a Yardstick `http` target can call it locally |
| **3. Admin views and reports** | Static HTML views built from the files: budgets and period-to-date spend, breaker timeline, trace explorer, daily report. `switchyard policy diff`. | Views render from the golden cases with no network; every number on a view ties to a ledger or trace line |
| **4. Demo and closeout** | A scripted demo (an outage, a budget limit, a cache hit, a route change), README, release notes, review. | A clean clone runs the demo offline from the README |

Not in any phase: deployment, real providers, semantic caching.
