# Daily cost report: c02-provider-outage

Illustrative prices, fake providers. Not deployed. Every number below was computed by
Switchyard from a golden case run under a fake clock. Money is integer micro-dollars (µUSD)
unless the column says USD.

| date | team | requests | ok | no_route | denied_budget | failed_exhausted | bad_request | unknown_team | cache_hits | attempts | failed_attempts | input_tokens | output_tokens | cost_micro | cost_usd | avoided_micro (estimate, not in the ledger) | adjustments_micro | requests with a reserve above the soft limit | requests with a reserve above the hard limit | downgraded | fallback_used | period-to-date spent_micro | soft_limit_micro | hard_limit_micro | remaining_micro |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-11-02 | support | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 11 | 5 | 2400 | 72 | 650 | 0.000650 | 0 | 0 | 0 | 0 | 0 | 5 | 650 | 1000000000000 | 1000000000000 | 999999999350 |

Notes:

- Requests, status counts, cache hits, flag counts and `avoided_micro` are counted on the UTC
  day the request arrived. Attempts, tokens and cost are counted on the UTC day of each attempt's
  reserve, so a request that arrives before midnight and makes an attempt after it is a request
  on the first day and a cost on the second.
- `avoided_micro` is an estimate of what cache hits would have cost. It is not in the ledger.
- Period-to-date `spent_micro` is for the UTC calendar month of the row, as of the end of the
  row's day.
  `remaining_micro` is `hard_limit_micro - spent_micro`. A team with no config shows blank limits.
- This report ran `reconcile` first and found no difference between the traces and the ledger.
