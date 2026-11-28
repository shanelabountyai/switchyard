# Switchyard: Phase 2 plan (HTTP gateway against fakes)

Scope: an HTTP front on the Phase 1 engine. One route, `POST /v1/chat/completions`, with an OpenAI-compatible
request and response shape, bound to `127.0.0.1` only, in front of the same fake providers. The ledger and the
traces are also written to disk as JSON Lines. **No real provider, no API key, no outbound network call, no live
model call, nothing deployed.** Prices stay illustrative.

This file is a plan. It was written on 2026-11-27 in an unattended run that built nothing: no product code and
no dependency change. Judgment calls are rows D-047 to D-062 in `DECISIONS.md`. The next run builds from these
defaults unless Shane changes a row.

Done means: every exit criterion in section 2 passes, in CI where the criterion says CI.

Where this plan and the PRD disagree, the PRD wins and the difference is recorded. Where a worked example and
this plan disagree, the worked example wins.

## 1. What Phase 1 left for Phase 2 to build on

| Phase 1 piece | How Phase 2 uses it |
|---|---|
| `Engine.handle_raw(raw, arrival_ms, fake_output_tokens)` | The HTTP layer builds the same raw mapping a case file holds and hands it to the engine. Validation, statuses and the trace stay in the engine |
| `Ledger`, `Health`, `ExactCache` each hold one lock | These are the shared state under concurrent requests. Phase 2 adds three more: one in the engine (seen ids and the trace list), one for the request-id counter, and one inside the fake clock's new `advance_to` (section 6) |
| `FakeProvider` (scripted windows) | Still the only provider. In a served gateway the script's time zero is the server's start |
| `FakeClock` / `SystemClock` | Tests and the golden replay inject `FakeClock`. `switchyard serve` uses `SystemClock`, so a fake "takes" its scripted latency by sleeping |
| `cases.load_case` / `run_case` | The reference for the HTTP replay: the same case run in-process gives the bytes the HTTP run must equal |
| `canonical_json` (rejects floats) | Every response body is written with it, so a float in a response is an error, not a rounding surprise |
| `tests/invariants/test_sw09_fakes_only.py` | Extended, not loosened: one package may import the HTTP library, and the lock file is checked too |

Three facts about the Phase 1 code shape this plan:

1. **The engine is synchronous.** It blocks while a fake "takes" its latency. So the HTTP layer should be
   synchronous too, with one thread per request. That is what makes concurrency real in Phase 2.
2. **The engine returns only a trace.** The response needs the fake's text and the serving model, and on a cache
   hit the stored text. Section 6 adds a result object.
3. **The engine advances the clock by a latency.** With two requests in flight on one fake clock that adds the
   two latencies together. Section 6 changes "advance by latency" to "wait until start plus latency", which is
   the same thing when requests run one at a time.

## 2. Exit criteria

Each one names the check that decides it. "CI" means the `check` job on the Phase 2 pull request head.

| # | Criterion | Decided by |
|---:|---|---|
| X1 | The gates pass: `uv sync --locked`, `ruff check`, `ruff format --check`, `mypy` (strict, `src` and `tests`), `pytest`, and the example-report drift step | CI |
| X2 | Phase 1 is untouched where it is a contract. `git diff --exit-code main -- fixtures/cases fixtures/catalog fixtures/policy examples/provider-outage-report.md` prints nothing. No existing test function in a Phase 1 test file is edited or removed. The one Phase 1 test file that changes is `tests/invariants/test_sw09_fakes_only.py`: its forbidden list only grows and its one exception is the one section 10 states. Every other new test is in a new file | The command, plus `git diff main --stat -- tests/` in the pull request body showing one modified file and the rest added |
| X3 | **The 15 golden cases replay through HTTP with identical outputs** (D-055). For each of the 14 sequential cases, the trace JSON Lines and the ledger JSON Lines produced through HTTP are byte-identical to those `run_case` produces in-process, and the end breaker states, cache sizes and daily report rows are equal | `tests/http/test_golden_http.py::test_sequential_case_is_byte_identical` (14 parameters), CI |
| X4 | The fifteenth case, `c07-concurrent-overspend`, replays through HTTP with three real overlapping requests held by a test gate: the ledger JSON Lines are byte-identical to the in-process run, and A is 200, B is 429, C is 200 | `tests/http/test_golden_http.py::test_c07_overlapping_requests`, CI |
| X5 | Every worked example is asserted through HTTP against the case file, not against the HTTP run itself. For each request of the 14 sequential cases: the case's `expect` block is applied to the trace of the HTTP run with the same checker Phase 1 uses; the HTTP code is the one section 5.4 maps from `expect.status`; and the response fields listed under "projection" in 5.4 equal the trace. Each case's `expect_end` block (balances, breakers, ledger count, cache sizes, report rows) is applied to the HTTP run, which is how WE-30 is asserted. WE-17 is asserted by X4. A test checks that these three together name every ID from WE-01 to WE-30 | `tests/http/test_golden_http.py::test_request_expectations_over_http`, `test_end_expectations_over_http`, `test_every_worked_example_is_covered`, CI |
| X6 | Deterministic over HTTP (SW-01): each case replayed twice gives byte-identical response bodies, trace file and ledger file | `tests/http/test_determinism_http.py::test_http_replay_twice`, CI |
| X7 | Each of the six statuses maps to its HTTP code with the body shape in section 5.4. An unknown path is 404 and a wrong method is 405, and neither writes a trace | `tests/http/test_app.py`, CI |
| X8 | One trace per chat request (AC-E1 over HTTP). With fake scripts that bill nothing and files that can be written (the two preconditions section 5.6 explains), any `POST /v1/chat/completions` writes exactly one trace line and never answers 500. Decided by a hypothesis test over random bytes and random headers, plus one named example for each input random bytes will not find: a lone surrogate in message content (`"a\ud800"`), JSON nested 100,000 deep, an integer literal of 5,000 digits in the body, a 5,000-digit limit header, a body over the cap, and a missing `Content-Length` | `tests/http/test_app.py::test_any_body_gives_exactly_one_trace`, `test_hostile_bodies_give_one_trace_and_400`, CI |
| X9 | The server binds to `127.0.0.1` only. `switchyard serve --host` with any other value (`0.0.0.0`, `localhost`, `::1`, a LAN address) exits 2 before a server object exists and before any file is created in the data directory. A started server reports `127.0.0.1` as its address | `tests/http/test_serve.py::test_refuses_any_other_host`, `test_loopback_smoke`, CI |
| X10 | Fakes only (SW-09, extended). In `src/switchyard`, only `server/app.py` and `server/serve.py` import the HTTP library, and they import nothing else on the forbidden list. No file reads the environment. `uv.lock` names no HTTP client and no provider SDK | `tests/invariants/test_sw09_fakes_only.py`, CI |
| X11 | Integer money over HTTP (SW-07). No float object is created from a request body. In every response `cost_usd == format_usd(cost_micro)` and every money field is an `int` or that six-decimal string | `tests/http/test_mapping.py::test_body_parse_creates_no_float`; `tests/http/test_golden_http.py::test_money_fields_in_every_response`, which walks every response body of every replayed case; `tests/invariants/test_sw07_integer_money.py` (its scan already covers the new package), CI |
| X12 | No overspend under concurrent HTTP requests (SW-02). Fixture, all illustrative: team `support`, soft and hard limit 1,200 µUSD, `deny`, **cache off**, no opening balance, fake controls on; 32 triage requests with distinct ids, each sent with fake input tokens 400, `max_tokens` 50 and fake output tokens 12, so each holds 120 and costs 90. "Accepted" is the number of `reserve` entries, which must equal the number of 200s. (a) Gated: the first 10 calls are held in flight, the other 22 are then posted and all are 429, the 10 are released: exactly 10 accepted and 22 refused. (b) Free-running: 32 threads start on a barrier, 20 runs; accepted is between 10 and 13 and 200s plus 429s make 32. In both, `committed <= 1,200` at every prefix of the ledger, `held` ends 0 and `reconcile` is clean | `tests/http/test_concurrency.py::test_budget_with_ten_calls_held`, `test_budget_holds_under_free_running_threads`, CI |
| X13 | One probe at a time, end to end. With `alder` half-open and its probe held by the gate, a second request skips `alder` (`breaker_open_skip`) and is served by `birch`. Across both traces there is exactly one `probe` event | `tests/http/test_concurrency.py::test_one_probe_in_flight`, CI |
| X14 | "Record while open" runs end to end (PRD 5.2; Phase 1 listed it as not verified). Two calls are allowed while `alder` is closed; the first failure opens the breaker; then the second call fails. Asserted: the breaker is still `open` with the same `opened_at`; the second request's events hold `no_retry:breaker_open` and no `breaker_opened`; across both traces there is exactly one `breaker_opened`; and the breaker has no call in flight afterwards (the same check `tests/test_breaker.py::test_record_while_open_changes_nothing_else` uses) | `tests/http/test_concurrency.py::test_record_while_open`, CI |
| X15 | The retry budget cannot be double-spent. Two requests fail on `alder` while both are in flight, with one retry allowed: exactly one `retry` event, and the other request has `no_retry:retry_budget_exhausted` | `tests/http/test_concurrency.py::test_retry_grant_is_atomic`, CI |
| X16 | A repeated `request_id` sent twice at once: exactly one is processed and the other is `bad_request` (400). 20 runs | `tests/http/test_concurrency.py::test_duplicate_request_id_race`, CI |
| X17 | Files on disk equal memory. After a run, the ledger file equals `Ledger.to_jsonl()` and the trace file equals `to_jsonl(traces)` byte for byte; `Ledger.from_jsonl` of the file gives the same balances; `reconcile` over the two files is clean (same preconditions as X8). `serve` exits 2 if either file already exists | `tests/http/test_sinks.py`, CI |
| X18 | The fake tokenizer follows the rule in section 5.2, and two requests that differ only in line endings, Unicode composition or outer whitespace count the same tokens, so the second is `cache: hit` over HTTP with no fake-control header | `tests/http/test_tokenizer.py`, `tests/http/test_app.py::test_normalized_twin_is_a_cache_hit`, CI |
| X19 | No message text on disk (AC-E5 over HTTP): a marker string sent as message content appears in neither file | `tests/http/test_sinks.py::test_no_message_text_on_disk`, CI |
| X20 | A Yardstick `http` target config for the local gateway is committed, written from Yardstick's real target format (build step 1 reads it). Every response field it reads exists at the path it names, with the type Yardstick expects, in a real response from the in-process client | `tests/http/test_yardstick_target.py`, CI |
| X21 | That Yardstick target has been run against `switchyard serve` on the same machine, and the command and its output are pasted into `NEXT.md` | A person reading `NEXT.md`. If the build run cannot run Yardstick, X21 stays **open**, the pull request says so, and Shane runs it (D-059). Phase 2 is not closed until X21 is met |
| X22 | `README.md` has one status line that says Phase 2 is built and tested offline, with fakes, not deployed. `NEXT.md` is updated. New decisions are rows in `DECISIONS.md` | The pull request diff |

X12's bounds, worked by hand (all numbers illustrative): each triage request holds 120 and costs 90. A reserve is
refused only when `committed + 120 > 1,200`, so `committed > 1,080`, which needs at least ten accepted reserves:
the lower bound is 10. When the last accepted reserve is made, each of the other N − 1 accepted requests counts
at least 90 toward `committed`, so `90 × (N − 1) + 120 <= 1,200` and N is at most 13. The test recomputes both
bounds from the fixture numbers. Under a fake clock nothing forces the free-running threads to overlap, so most
of those runs will be close to sequential; the gated variant is the one that proves the hold blocks.

## 3. HTTP library: Werkzeug 3.1.9 (D-048)

Three candidates were compared against what the repository requires. Versions are the latest on the package
index on 2026-11-27 (read from `https://pypi.org/pypi/<name>/json`). Package counts come from `uv lock` in a
throwaway project that held this repository's exact pins plus the candidate. The throwaway project is not
committed, so the figures in this table cannot be re-run from this repository. The build run's own `uv lock`
is the check on the Werkzeug column.

| | FastAPI 0.165.0 | Flask 3.1.3 | **Werkzeug 3.1.9** |
|---|---|---|---|
| Released (index upload date) | 2026-11-18 | 2026-02-19 | 2026-09-27 |
| Python | `>=3.11` | `>=3.9` | `>=3.9` |
| Resolves with this repo's pins | yes | yes | yes |
| Packages added to the lock (now 28) | 11: `fastapi`, `starlette`, `anyio`, `idna`, `opentelemetry-api`, `click`, plus a server (`uvicorn` 0.54.0, `h11`) and, for the test client only, `httpx` 0.29.0, `httpcore`, `certifi` | 7: `flask`, `werkzeug`, `jinja2`, `markupsafe`, `itsdangerous`, `blinker`, `click` | 2: `werkzeug`, `markupsafe` |
| An HTTP **client** package in the lock | yes (`httpx`, needed by the test client) | no | no |
| Strict mypy | a ten-line app passed `mypy --strict` | a ten-line app passed | a sixty-line spike passed; ships `py.typed` |
| Fit with a synchronous engine | handlers are async; the blocking engine call has to be pushed to a thread pool | synchronous, one thread per request | synchronous, one thread per request |
| In-process test client | yes, through `httpx`. The import printed a deprecation warning asking for `httpx2` | yes, built in (it is Werkzeug's) | yes, built in |
| pydantic | built around pydantic request models | none needed | none needed |
| Server for `127.0.0.1` | needs `uvicorn` | Werkzeug's `make_server` | `make_server` |

Why Werkzeug:

- **The pydantic request layer would not be used.** A malformed request must reach the engine so that it gets a
  `bad_request` trace (PRD AC-E1, D-041), and the body must be parsed without creating a float (SW-07). FastAPI's
  own validation answers a malformed body before the engine sees it, and its JSON parsing makes floats. So the
  one thing FastAPI adds over a plain HTTP library is the thing this gateway has to bypass. pydantic still
  validates every request, inside the engine, as in Phase 1.
- **The lock file stays free of any HTTP client.** SW-09 forbids `httpx` in `src/`. With Werkzeug no client
  library is installed at all, and X10 can check the lock file.
- **Least weight.** Two packages, against seven and eleven.
- **It matches the engine.** One thread per request, blocking calls, no event loop. The spike ran the in-process
  client from two threads with one request held at a gate, which is what X4 and X13 to X15 need, and ran
  `make_server("127.0.0.1", 0, app, threaded=True)` with a standard-library client over loopback.
- **Test speed.** The in-process client calls the WSGI function directly: no socket and no server thread. No
  timing was measured and none is claimed.
- **Environment.** A search of Werkzeug 3.1.9 for `os.environ` found reads only in `run_simple` and the
  reloader. The plan uses `make_server`, not `run_simple`, and no reloader or debugger.

What is given up: Werkzeug is a WSGI library, not a full framework. Routing here is two paths, so the app is one
function. Its server is a development server and its own documentation says not to use it in production. That
fits a project that is never deployed, and the README line says so. Flask is the same server and the same test
client with an application layer on top. Changing to Flask later is a small diff inside `server/app.py`.

What was not checked: FastAPI and Flask were checked only for resolving and for a ten-line app under strict
mypy. Nothing here is a benchmark.

The pin `werkzeug==3.1.9` goes into `[project].dependencies` in the build run, followed by `uv lock`. **This run
did not change `pyproject.toml` or `uv.lock`.** If the pin does not resolve in the build run, stop and report
the resolver's message.

## 4. Repository layout after Phase 2

New files are marked `+`. Changed files are marked `~`. Everything else is untouched.

```text
~ pyproject.toml            ~ uv.lock            ~ README.md (one status line)
  src/switchyard/
~   engine.py   ~ ledger.py   ~ breaker.py   ~ clock.py   ~ models.py   ~ config_io.py   ~ cli.py
+   server/__init__.py      a docstring only, so importing a pure module does not import werkzeug
+   server/tokenizer.py     the fake token counter (pure)
+   server/mapping.py       HTTP request -> raw engine request; trace -> HTTP response (pure)
+   server/sinks.py         JSON Lines files (file I/O)
+   server/wiring.py        build the engine, sinks and Gateway (no werkzeug; used by serve and the replay)
+   server/app.py           the WSGI app (imports werkzeug)
+   server/serve.py         load config, refuse a non-loopback host, run the server (imports werkzeug)
  tests/
~   invariants/test_sw09_fakes_only.py     (the only existing test file that changes)
+   http/__init__.py   + http/replay.py (case -> HTTP requests)   + http/gated.py (test-only gate)
+   http/test_tokenizer.py  + http/test_mapping.py  + http/test_app.py  + http/test_sinks.py
+   http/test_golden_http.py  + http/test_determinism_http.py  + http/test_concurrency.py
+   http/test_serve.py  + http/test_serve_cli.py
+   http/test_yardstick_target.py
+   test_engine_phase2.py   (new engine, clock and retry-budget behaviour; no Phase 1 test file is edited)
  fixtures/
+   teams/illustrative-teams.yaml          (team budgets for `serve`; illustrative)
  examples/
+   yardstick/switchyard-http-target.yaml  (a Yardstick `http` target for 127.0.0.1)
  docs/switchyard/
~   PRD.md (3.2, 3.6 and 8.1, see section 6)   ~ DECISIONS.md   ~ ../../NEXT.md
```

`.github/workflows/ci.yml` does not change. `pytest` already runs everything under `tests/`.

## 5. The HTTP contract

### 5.1 Request

`POST /v1/chat/completions`. The body is a JSON object, UTF-8, at most 1,048,576 bytes.

| Where | Name | Maps to (PRD 3.6) | Rule |
|---|---|---|---|
| header | `X-Switchyard-Team` | `team` | Required |
| header | `X-Switchyard-App` | `app` | Required |
| header | `X-Switchyard-Use-Case` | `use_case` | Required |
| header | `X-Switchyard-Task-Class` | `task_class` | Required |
| header | `X-Switchyard-Data-Tags` | `data_tags` | Optional. Comma-separated, each trimmed. Absent or an empty header means no tags. An empty element (`pii,,x`) is a `bad_request` |
| header | `X-Switchyard-Request-Id` | `request_id` | Optional. When absent the server assigns `sw-00000001`, `sw-00000002`, … from a counter under a lock. No randomness. A client id that starts with `sw-` is a `bad_request`, so an assigned id can never collide with a client's |
| header | `X-Switchyard-Max-Latency-Ms`, `X-Switchyard-Max-Cost-Micro` | `max_latency_ms`, `max_cost_micro` | Optional. 1 to 18 digits, nothing else. They can only tighten the rule (PRD AC-A5) |
| body | `messages` | `messages` | Required. A list of `{role, content}` with string content. Any other key in a message is a `bad_request` |
| body | `max_tokens` | `max_output_tokens` | Required, a whole number of at least 1. The budget hold is sized from it, so there is no default (D-049) |
| body | `temperature` | `temperature` | Optional JSON number. Absent means `"0"`. See 5.3 |
| body | `model` | `model_hint` | Optional string of at most 200 characters. It is a hint. The routing policy picks the model. It is recorded on the trace (D-054) |
| body | `stream`, `n`, `tools`, `functions` | none | `stream: true`, `n` other than 1, and a `tools` or `functions` key with any value (an empty list too) are a `bad_request` (PRD section 12 non-goals). Other unknown body keys are ignored |

There is no authentication. The team is whatever the header says (PRD section 12). That is safe only because the
server listens on `127.0.0.1`.

The body cap is enforced before parsing: a missing or non-numeric `Content-Length`, or one over the cap, is a
`bad_request`, and the app never reads more than the cap. A `X-Switchyard-*` header sent twice is a
`bad_request`.

**A request the mapper cannot map still gets a trace.** Unparseable JSON (including nesting deep enough to
raise `RecursionError` and an integer literal too long for Python to convert), a duplicate key, a body that is
not an object, a body over the cap, any string that cannot be encoded as UTF-8 (a lone surrogate), a header
that is not printable ASCII, a bad limit header, or a fake-control header when controls are off: the mapper returns the fields it could read plus a problem string, and the engine
writes a `bad_request` trace with those fields (`Engine.reject`, section 6). The HTTP answer is 400 with the
problem string as `error.message`. The message never repeats message content. When the request id header
itself was unusable, the trace's `request_id` is `""` (as D-041 already allows) and the response carries no
`X-Switchyard-Request-Id` header.

### 5.2 The fake tokenizer

`input_tokens` is the sum, over the messages, of `ceil(n / 4)` where `n` is the number of Unicode code points in
the message content **after** the cache normalization of PRD 7.1 (NFC, `\r\n` and `\r` to `\n`, outer whitespace
removed), using `cache.normalize_content` itself, not a copy of the rule. Roles are not counted. The mapper has
already rejected any content that is not a string. The ceiling is integer arithmetic: `-(-n // 4)`.

It counts normalized content so that two requests the cache treats as the same text also get the same
`input_tokens`. `input_tokens` is part of the cache key, so counting raw text would make WE-25's "two trailing
spaces" a miss when it is served over HTTP (D-051).

It is a fake. It is named `fake_token_count`, its docstring says it is not a real tokenizer, and the response
says `"tokenizer": "fake-4-chars"` in the `switchyard` block.

### 5.3 Temperature without floats

The body is parsed by `json.loads` with three hooks: `parse_float` wraps the number's literal text in a marker type, `NumberText` (a `str` subclass), instead of
making a float, `parse_constant` rejects `NaN` and `Infinity`, and `object_pairs_hook` rejects a duplicate key.
A `NumberText` is accepted in exactly one place, `temperature`. Anywhere else (`"content": 1.5`, `"model":
1.5`, `"max_tokens": 50.0`) it is a `bad_request`, and a plain JSON string in `temperature` (`"0.7"`) is a
`bad_request` too: the marker type is what tells the two apart. `temperature` then becomes the decimal string
the engine expects:

- an integer literal is written as is (`0` → `"0"`, `1` → `"1"`);
- a literal with a fraction loses its trailing zeros and a bare trailing dot (`0.70` → `"0.7"`, `0.0` → `"0"`);
- `-0` and `-0.0` become `"0"`;
- an exponent form (`1e-1`), a string, a boolean or `null` is a `bad_request`.

So `0`, `0.0` and an absent temperature all mean `"0"` and can be cached. Anything else bypasses the cache with
`bypass_nondeterministic`, as in Phase 1 (D-050).

### 5.4 Response

Every body is canonical JSON (sorted keys, no float). The `Content-Type` is `application/json`. The
`X-Switchyard-Request-Id` response header carries the request id.

| Status | HTTP | Body |
|---|---:|---|
| `ok` | 200 | the completion object below |
| `bad_request` | 400 | the error object below |
| `unknown_team` | 403 | error object |
| `no_route` | 422 | error object |
| `denied_budget` | 429 | error object, plus `switchyard.budget` |
| `failed_exhausted` | 503 | error object |
| a fake adapter raised (D-044) | 500 | `{"error": {"code": "internal_error", ...}}`. No trace, as in Phase 1. The hold is released |
| unknown path / wrong method | 404 / 405 | `{"error": {"code": "not_found" or "method_not_allowed"}}`. No trace: it is not a chat request |

`GET /healthz` answers 200 with `{"status": "ok", "fakes_only": true, "illustrative": true}` so a local script
can wait for the server. It writes no trace.

The completion object (200):

```json
{
  "id": "<request_id>",
  "object": "chat.completion",
  "created": 1793610000,
  "model": "alder-small",
  "choices": [{"index": 0, "finish_reason": "stop",
               "message": {"role": "assistant", "content": "[fake alder-small] 1a2b3c4d5e6f"}}],
  "usage": {"prompt_tokens": 400, "completion_tokens": 12, "total_tokens": 412},
  "latency_ms": 400,
  "cost_usd": "0.000090",
  "switchyard": {
    "request_id": "<request_id>", "status": "ok", "rule_id": "support-triage",
    "chain": ["alder-small", "birch-small"], "cache": "bypass_team_opt_out", "flags": [],
    "attempts": 1, "cost_micro": 90, "avoided_micro": 0, "model_hint": "",
    "config_version": "sha256:…", "tokenizer": "fake-4-chars", "fakes_only": true, "illustrative": true
  }
}
```

The values above are illustrative and follow WE-01. The twelve characters after the model name are the start of
the cache key; the ones shown are made up. Rules:

- `created` is `finished_ms // 1000`. `latency_ms` is `finished_ms - arrived_ms` from the trace. Under
  `SystemClock` that is the fake's scripted latency plus scheduling time. It is not a performance number.
- `cost_usd` is `format_usd(cost_micro)`. `latency_ms` and `cost_usd` are top-level because PRD section 9 says
  the response carries them for Yardstick's `http` target.
- `model` is the model that served the answer. On a cache hit it is the model stored with the entry, and
  `content` is the stored text.
- `finish_reason` is always `"stop"`. The fake does not model why it stopped, and a cache entry does not hold
  token counts, so anything finer would be made up (D-062).
- **Projection.** These response fields must equal the trace: `switchyard.request_id`, `status`, `rule_id`
  (`route.rule_id`, or `""`), `chain` (`route.chain`, or `[]`), `cache`, `flags`, `attempts`
  (`len(trace.attempts)`), `cost_micro`, `avoided_micro`, `model_hint`, `config_version`; top-level `cost_usd`
  (`format_usd(trace.cost_micro)`) and `latency_ms`.
- `usage` is **billed** tokens: the sums of `attempts[].input_tokens` and `attempts[].output_tokens` in the
  trace. A cache hit bills nothing, so its usage is zeros and `switchyard.cache` says `hit` (D-053).

The error object (every status but `ok`):

```json
{
  "error": {"type": "switchyard_error", "code": "denied_budget", "message": "the team budget refused this request"},
  "latency_ms": 0,
  "cost_usd": "0.000000",
  "switchyard": {"request_id": "…", "status": "denied_budget", "rule_id": "support-triage",
                 "chain": ["alder-small", "birch-small"], "cache": "bypass_team_opt_out", "flags": [],
                 "attempts": 0, "cost_micro": 0, "avoided_micro": 0, "model_hint": "",
                 "config_version": "sha256:…", "tokenizer": "fake-4-chars", "fakes_only": true,
                 "illustrative": true,
                 "budget": {"amount_micro": 120, "spent_micro": 9950, "held_micro": 0, "ceiling_micro": 10000}}
}
```

`error.code` is the status, so a caller can branch on it. `switchyard.budget` is the detail of the request's
last `budget_refused` event and appears only on 429. `cost_usd` can be above zero on an error: charges from
earlier attempts stand (PRD 6.3). `rule_id` is `""` and `chain` is `[]` when the request ended before routing.

### 5.5 Fake controls (for the golden replay)

The case files give each request its `input_tokens` and say how many tokens the fake returns
(`fake_output_tokens`). To replay them unchanged, the gateway accepts two more headers **only when it was
created with `fake_controls=True`**:

- `X-Switchyard-Fake-Input-Tokens`: use this count instead of the fake tokenizer.
- `X-Switchyard-Fake-Output-Tokens`: what a successful fake call returns, capped at `max_tokens`.

`switchyard serve` creates the gateway with controls off unless `--fake-controls` is given. With controls off,
either header is a `bad_request`. They steer a fake, never a real provider, and the names say so (D-052).

### 5.6 The two ways a request can end without a trace

Both are faults in the gateway's own setup, not request outcomes, and X8 and X17 hold only when neither
happens.

1. **A fake adapter raises** (D-044): the engine releases the hold, gives the breaker permit back and re-raises.
   Over HTTP that is a 500 with no trace, and it leaves a reserve and a release in the ledger that no trace
   claims, so `reconcile` reports them from then on. The only known cause is a script window that bills more
   tokens than a request has. So `serve --scripts` **refuses a script with `billed_input_tokens` or
   `billed_output_tokens`** (exit 2). Case files may still use them, because a case's requests are written to
   fit (WE-12). D-044 itself is not changed (D-061).
2. **A file write fails.** The gateway is fail-stop: the error is not swallowed, the request answers 500, the
   gateway marks itself failed and answers 500 `storage_failed` to every later request, and `serve` exits 1.
   Memory can then be one line ahead of the file; the file is still a valid prefix.

Stopping `serve` with Ctrl-C stops accepting requests and does not wait for calls in flight. A request cut off
that way can leave an open reserve at the end of the ledger file, which `reconcile` reports. Closing such holds
is `switchyard sweep`, a Phase 3 command.

## 6. Changes to Phase 1 modules

Each change is small, and each is guarded the same way: **every Phase 1 test passes with no edit** (X2). If one
of these changes needs a Phase 1 assertion or a golden value to move, stop and report both derivations.

| File | Change | Why |
|---|---|---|
| `engine.py` | `serve_raw(raw, *, arrival_ms=None, fake_output_tokens=None) -> Served`, where `Served` holds `trace`, `text` and `model`. `handle` and `handle_raw` stay and return `.trace` | The response needs the text and the serving model, including on a cache hit |
| `engine.py` | `reject(raw) -> Served`: the `bad_request` branch of `handle_raw`, callable when the HTTP mapper could not build a request | One trace per chat request (X8) |
| `engine.py` | One lock around "have I seen this `request_id`" and around appending a trace. An optional `on_trace` callback is called under that lock. An optional `keep_traces` flag (default true) lets `serve` skip the in-memory list | Two threads could both pass the duplicate check (X16). File order must equal memory order (X17) |
| `clock.py`, `engine.py` | The `Clock` protocol gains `advance_to(target_ms)`. `FakeClock.advance_to` sets the time to `max(now, target)` under its lock, in one step. `SystemClock.advance_to` sleeps for what is left. After a provider call the engine calls `clock.advance_to(started + latency)` instead of `clock.advance(latency)`, then reads the finish time from the clock as before | With one request at a time `clock.now_ms() == started`, so nothing changes. With two in flight on a fake clock, each still finishes at its own start plus latency, which X4 needs to get c07's timestamps (D-056) |
| `engine.py`, `breaker.py` | `RetryBudget.take(now, start_ts) -> bool` checks the budget and records the retry in one step, with the retry's start time (`now + backoff_ms`). If that retry then does not start (breaker or budget refused it), the engine gives it back with `RetryBudget.give_back(start_ts)`. `can_retry` and `note_retry` stay for the Phase 1 tests | "May I retry" and "I retried" were two steps. Two failing requests could both be granted the last retry (X15). Giving an unused grant back keeps one-at-a-time behaviour exactly as Phase 1 |
| `breaker.py` | `RetryBudget` drops old timestamps when it counts. It prunes against the **largest `now` it has seen**, minus the window, so a thread that arrives with an older `now` cannot make it forget entries a later caller still needs, and it never under-counts | A review item carried from Phase 1. A server runs for a long time; the lists must not grow without limit |
| `ledger.py` | An optional `on_append` callback, called inside the existing lock after each entry is appended | The ledger file gets each line in `seq` order (X17) |
| `models.py` | `Request.model_hint: str = ""` (at most 200 characters) and `Trace.model_hint: str = ""`. Not in the cache key, not read by routing. `Message.content`, the name fields and `model_hint` reject a string that cannot be encoded as UTF-8, so the engine gives `bad_request` instead of raising inside `cache_key` | "The request's `model` field is treated as a hint and recorded" (`IMPLEMENTATION_PLAN.md`). A default of `""` keeps every existing trace valid |
| `config_io.py` | `load_teams_file(path)` and `load_scripts_file(path)`: the file wrappers `serve` needs around `load_teams` and `ScriptWindow` | `serve` has no case file to read them from |
| `cli.py` | New command `switchyard serve`. The import of `switchyard.server.serve` is inside the command function | `replay`, `explain` and `report` must not import the HTTP library |
| `PRD.md` 3.2, 3.6, 8.1 | 3.2 gains one sentence: "From Phase 2, a call finishes at its start time plus its latency, so two calls in flight do not add their latencies." The request table in 3.6 and the trace table in 8.1 each gain a `model_hint` row | The PRD lists every request and trace field, so a new field must be in it (CLAUDE.md: fix the PRD in the same pull request). A decision row in the build run records the edits |

What is **not** changed: routing, budget checks, the cache rules, the breaker state machine, the ledger's entry
kinds, the trace fields other than `model_hint`, the report, and every fixture.

Known limits that stay (all in "Not verified" or "Not in Phase 2" of the pull request): the seen-id set and the
in-memory ledger grow with every request; two identical requests in flight both miss the cache and both call
the fake; under `FakeClock` with free-running threads the timestamps are not meaningful, so X12(b) and X16 assert
invariants and counts, never times. The lone-surrogate change to `models.py` is the one place Phase 2 makes
the engine stricter than Phase 1; no case file or Phase 1 test sends such a string.

## 7. New modules

### `server/tokenizer.py` (pure)

- **In / out.** `fake_token_count(messages: Sequence[Mapping[str, object]]) -> int` (section 5.2). `TOKENIZER_ID
  = "fake-4-chars"`.
- **Invariants.** Integer arithmetic only. Equal for inputs that are equal after normalization.
- **Tests.** A table: `""` → 0, 1 to 4 characters → 1, 5 → 2, two messages sum. CRLF against LF, NFD against NFC,
  and outer whitespace give the same count. Inner whitespace and letter case can change it.

### `server/mapping.py` (pure; imports no HTTP library)

- **In / out.** `map_request(headers: Mapping[str, str], body: bytes, *, fake_controls: bool, assign_id:
  Callable[[], str]) -> Mapped`, where `Mapped` holds `raw` (the mapping for `Engine.serve_raw`),
  `fake_output_tokens` and `problem: str | None`. `http_status(status: Status) -> int`.
  `response_body(served: Served, problem: str | None) -> dict[str, object]`.
  `parse_body(body: bytes) -> object` (section 5.3).
- **Invariants.** Never creates a float. Never raises on any bytes: a bad input becomes a `problem`. `parse_body`
  catches `ValueError`, `RecursionError` and `UnicodeDecodeError`; header integers are matched against
  `[0-9]{1,18}` before `int()`. The error
  message never contains message content.
- **Tests.** One test per row of the table in 5.1 and per rule in 5.3, including `"content": 1.5` and
  `"temperature": "0.7"` (both `bad_request`). `test_body_parse_creates_no_float` walks the parsed value. A hypothesis property over random bytes: `map_request` returns and does not raise. A
  round-trip property: for a valid Phase 1 request (ASCII names, a canonical temperature), turning it into
  headers and a body with `tests/http/replay.py` and mapping it back gives the same raw mapping. That property
  is what lets X3 trust the transport.

### `server/sinks.py` (file I/O)

- **In / out.** `JsonlFile(path)` opens the file for exclusive creation, and `write(line: str)` appends the line
  and a newline and flushes. `ledger_line(entry)` and `trace_line(trace)` give exactly the lines
  `Ledger.to_jsonl` and `trace.to_jsonl` give.
- **Invariants.** An existing file is an error, never overwritten or appended to (D-057). One line per entry.
- **Tests.** X17 and X19. Opening an existing path raises.

### `server/wiring.py` (file I/O; imports no HTTP library)

- **In / out.** `Gateway` (a dataclass: `engine`, `fake_controls`, the locked id counter, a `failed` flag).
  `build_gateway(policy, catalog, teams, scripts, clock, *, data_dir: Path | None, fake_controls: bool,
  opening=()) -> Gateway`. It creates `data_dir` if missing, opens `ledger.jsonl` and `traces.jsonl` there
  (when `data_dir` is given), creates the `Ledger` with `on_append` set **before** any opening adjustment is
  posted, so the file holds line 1, then the `Health`, the cache, the fake providers (script time zero is
  `clock.now_ms()`), and the `Engine` with `on_trace`. `serve` and `tests/http/replay.py` both call it, so the
  replay tests exercise the same wiring the server uses.
- **Tests.** Covered by X3 and X17. One test: the ledger file's first line is the opening adjustment.

### `server/app.py` (imports `werkzeug`)

- **In / out.** `create_app(gateway: Gateway) -> WSGIApplication`. `fake_controls` lives on the `Gateway`. One function handles a request: read the raw bytes (never the
  library's own JSON parsing), call `map_request`, call `engine.serve_raw` or `engine.reject`, build the body
  with `response_body`, write it with `canonical_json`.
- **Invariants.** No clock read, no environment read, no outbound call. The only two sources of a 500 are the
  ones in 5.6. The app is written with `@Request.application`, so no name `environ` appears in the source and
  the SW-09 scan, which flags that name, is not relaxed for it.
- **Tests.** X7, X8, X18's cache-hit test, the 500 path with a broken script, and the response rules in 5.4 on
  WE-01 (ok), WE-25 (hit), WE-15 (429 with `budget`), WE-04 (422), WE-13 (400 from the fake).

### `server/serve.py` (imports `werkzeug`; file I/O)

- **In / out.** `LOOPBACK = "127.0.0.1"`. `serve(paths, host, port, fake_controls, clock,
  make_server=werkzeug.serving.make_server)` works in this order, so a refused start leaves nothing behind:
  (1) refuse any host that is not exactly `LOOPBACK`; (2) load and validate the catalog, policy, teams and
  scripts with `config_io`, refusing a script that bills tokens (5.6); (3) refuse if `ledger.jsonl` or
  `traces.jsonl` exists in the data directory; (4) bind the server socket with a placeholder app; (5) only
  then call `wiring.build_gateway`, which creates the files, and attach the real app; (6) serve with
  `threaded=True` and the access log switched off (the trace is the record, and the library's log line reads
  the real clock).
- **CLI.** `switchyard serve --catalog PATH --policy PATH --teams PATH [--scripts PATH] --data-dir PATH [--host
  127.0.0.1] [--port 8600] [--fake-controls]`. Port 8600 is a proposal (D-058). Exit 2 on a bad file, an existing ledger or trace file, or any
  other host. `--host` exists so that the refusal is visible and tested. The first line it prints is "Fake
  providers and illustrative prices only. Listening on 127.0.0.1 only. Not for deployment."
- **Tests.** X9, including that a refused host and a port already in use leave the data directory empty.
  `test_loopback_smoke` starts the server on port 0 in a thread with a `FakeClock`, posts WE-01
  with the standard library's `http.client` (tests may import it; `src/` may not), reads a 200, and shuts down.
  It is the only test that opens a socket, and the socket is loopback. A unit test of `SystemClock.advance`
  (a two-millisecond wait moves `now_ms` by at least two) closes the Phase 1 "no test uses it" note.

### `fixtures/teams/illustrative-teams.yaml`

For `serve`. First line `# ILLUSTRATIVE.` Top level: `illustrative: true` and `teams:`, the mapping
`load_teams` already reads. Team `support` uses the PRD 6.2 example (soft 8,000, hard 10,000, `deny`, cache on,
TTL 60,000). The tests in section 2 build their own team configs and do not read this file. `marketing`, `research` and `legal`
use the "far from the limit" values the case files use. `--scripts` takes the same `scripts` mapping a case file
holds (D-060).

### `examples/yardstick/switchyard-http-target.yaml`

A Yardstick `http` target pointing at `http://127.0.0.1:8600/v1/chat/completions` with the four required
headers and the paths of `latency_ms` and `cost_usd`. The build run writes it against Yardstick's real target
format (read from the Yardstick repository in build step 1, not guessed). **If that format cannot send the
four headers or a `max_tokens` field, stop for a decision**: D-049 would have to change, and that is Shane's
call, not the build run's. X20 parses the file and checks each path against a
real response. The file says at the top that the providers are fakes and that a run over them measures wiring,
cost and latency numbers, not answer quality (PRD section 9).

## 8. Test helpers

- `tests/http/replay.py`: `to_http(case_request) -> (headers, body)`, the inverse of `map_request`, and
  `replay_case(loaded) -> HttpResult`. For a sequential case it builds the same engine `run_case` builds, with
  a `FakeClock`, via `wiring.build_gateway(..., fake_controls=True)` into a temporary data directory, wraps it in
  `create_app(gateway)`, and for each request advances the clock to
  the arrival time and posts through `werkzeug.test.Client`. It collects response statuses and bodies, the two
  files, breaker states, cache sizes and report rows. Requests are sent without a `model` field, so
  `model_hint` is `""` on both sides.
- `tests/http/gated.py`: `GatedProvider(inner, gates)` wraps a `FakeProvider`. `gates` maps a `request_id` to a
  list of `Gate(entered, release)` pairs, one per call that request makes on this provider, so a test can hold
  two requests in flight on the same provider and release them in the order it chooses. A call whose request
  has no gate left passes straight through. A gated call sets `entered` and blocks on `release` (with a hard
  timeout that fails the test), then returns the inner result. It is a
  fake around a fake. Tests that use it state the interleaving with events; they do not sleep and hope.
- c07 through HTTP (X4): the case file has no `policy` and no request bodies, so the replay loads
  `fixtures/policy/illustrative-policy.yaml` and sends, for each of A, B and C, the triage request (team
  `support`, app `helpdesk`, use case `triage`, task class `classify`, fake input tokens 400, `max_tokens` 50,
  fake output tokens 12, one user message), and checks that the planned model is the `model` the step names.
  Opening balance posted as in `run_case`. Clock at 0: post A on a thread and wait for
  `entered` (A has reserved 120 at t=0). Advance to 1: post B, which is refused at once (429). Release A and **join A's thread**: the
  engine waits until 0 + 400, so A settles 90 at t=400. Only then advance to 401: post C, which reserves at 401 and
  settles at 801. Request ids are `A`, `B`, `C`; each request is the triage request with fake output 12. The
  five ledger lines must equal the in-process run's five lines byte for byte.

## 9. Build order

Tests first inside each step. Run the gates after each step. Commit each step on its own, naming files.

| Step | Work | Files |
|---:|---|---|
| 1 | Re-read `DECISIONS.md` for any row Shane changed. Read Yardstick's `http` target format and confirm it can send custom headers and `max_tokens`; if it cannot, stop for a decision. Add `werkzeug==3.1.9`, run `uv lock`. Extend SW-09 (section 10) | `pyproject.toml`, `uv.lock`, `tests/invariants/test_sw09_fakes_only.py` |
| 2 | Fake tokenizer | `server/__init__.py`, `server/tokenizer.py`, `tests/http/__init__.py`, `tests/http/test_tokenizer.py` |
| 3 | Request mapping and float-free parsing | `server/mapping.py`, `tests/http/test_mapping.py` |
| 4 | Phase 1 module changes (section 6). **Checkpoint: the whole Phase 1 suite passes unedited** | `engine.py`, `ledger.py`, `breaker.py`, `clock.py`, `models.py`, `config_io.py`, `tests/test_engine_phase2.py`, `docs/switchyard/PRD.md` |
| 5 | File sinks and wiring | `server/sinks.py`, `server/wiring.py`, `tests/http/test_sinks.py` |
| 6 | Response mapping and the WSGI app | `server/mapping.py`, `server/app.py`, `tests/http/test_app.py` |
| 7 | Replay helper and the golden tests through HTTP (X3, X5, X6) | `tests/http/replay.py`, `tests/http/test_golden_http.py`, `tests/http/test_determinism_http.py` |
| 8 | Gate helper, c07 (X4) and the concurrency tests (X12 to X16) | `tests/http/gated.py`, `tests/http/test_golden_http.py`, `tests/http/test_concurrency.py` |
| 9 | `serve`, the teams file, bind refusal, loopback smoke | `server/serve.py`, `cli.py`, `fixtures/teams/illustrative-teams.yaml`, `tests/http/test_serve.py`, `tests/http/test_serve_cli.py` |
| 10 | Yardstick target file and its contract test. Run it locally if Yardstick is available (X21) | `examples/yardstick/switchyard-http-target.yaml`, `tests/http/test_yardstick_target.py` |
| 11 | README status line, `fixtures/README.md` (the teams file), `DECISIONS.md`, `NEXT.md`, independent review, draft pull request | docs |

## 10. How the build stays offline

- **There is no upstream.** A provider is a `FakeProvider` object in the same process. The gateway makes no
  outbound call of any kind, so there is nothing to mock and no key to hold.
- **Tests do not open sockets.** They call the WSGI function through `werkzeug.test.Client`. The one exception
  is `test_loopback_smoke`, which binds `127.0.0.1` on a free port and talks to itself.
- **SW-09 is extended, not loosened.** The forbidden list gains `werkzeug`, `flask`, `fastapi`, `starlette`,
  `uvicorn`, `wsgiref.simple_server` and `socketserver`. The scan then allows exactly one exception: `werkzeug`
  in `server/app.py` and `server/serve.py`. Every other rule still applies inside `server/`: no `socket`, no
  `http`, no `urllib`, no client library, no provider SDK, no environment read. A new test reads `uv.lock` and
  fails if it names `httpx`, `httpx2`, `requests`, `aiohttp`, `urllib3`, `httpcore` or any provider SDK on the
  list. A new test checks that no module outside `server/` imports `switchyard.server` except `cli.py`, inside
  the `serve` function.
- **No clock and no sleep** in `server/`: the existing `test_no_real_time_or_sleep_outside_clock` covers the
  new package as it stands.
- **CI** installs packages and runs tests. It holds no secret and its permissions stay `contents: read`.

## 11. PRD and plan items Phase 2 closes

The PRD's lettered acceptance criteria (AC-A to AC-E) were all closed in Phase 1 and stay green. Phase 2 closes
the PRD's Phase 2 row and the notes that point at it.

| Item | Where it is stated | Proved by |
|---|---|---|
| The golden cases replayed over HTTP give the same traces and ledger as Phase 1 | PRD 14 (Phase 2 exit gate); `IMPLEMENTATION_PLAN.md` | X3, X4, X5 |
| An OpenAI-compatible `POST /v1/chat/completions` on `127.0.0.1`, team and use case from headers | PRD 14, PRD 12 | X7, X9 |
| The OpenAI shape maps onto the request of PRD 3.6; `input_tokens` from a deterministic fake tokenizer | PRD 3.6 | `test_mapping.py`, X18 |
| JSON Lines ledger and traces on disk | PRD 14, PRD 12 | X17, X19 |
| Statuses map to HTTP codes, with a machine-readable body on a budget denial | `IMPLEMENTATION_PLAN.md` Phase 2 | X7 |
| A concurrent test at a nearly spent budget, and SW-02 holds | `IMPLEMENTATION_PLAN.md` Phase 2 exit gate | X12 |
| The half-open probe permit under real concurrency (AC-B3 end to end) | `IMPLEMENTATION_PLAN.md` Phase 2; PRD 5.2 | X13 |
| A `record` while the breaker is `open` | PRD 5.2 ("Phase 2"); Phase 1 pull request, "Not verified" | X14 |
| The one `server` package is the only exemption from SW-09 | PRD 10 (SW-09) | X10 |
| The response carries `latency_ms` and `cost_usd` for Yardstick's `http` target; a target config is committed and run locally | PRD 9 step 3; PRD 14; `IMPLEMENTATION_PLAN.md` | X20, X21 |
| AC-E1, AC-E5, SW-01, SW-02, SW-07 still hold with the HTTP layer in front | PRD 8.4, PRD 10 | X8, X19, X6, X12, X11 |
| "Phase 2 concurrency breaks the one-request-at-a-time assumption" | `IMPLEMENTATION_PLAN.md` risks | section 6 changes; X4, X13 to X16 |

PRD section 9 (the Yardstick gate) stays a design. Phase 2 makes the gateway callable by a Yardstick `http`
target. It does not build the gate, `policy diff`, or any CI step that runs Yardstick.

## 12. Open items carried from Phase 1, and the default that stands

Pull requests #1 and #2 were merged on 2026-11-27. None of the rows below has an answer recorded in
`DECISIONS.md`, so each default stands for the Phase 2 build. To change one, add a row that names it.

| Item | Default that stands in Phase 2 | What Phase 2 does with it |
|---|---|---|
| D-012: reserve per attempt, not per request | Per attempt | A 429 can come part-way through a chain after earlier attempts were charged. The error body's `cost_usd` shows those charges |
| D-014: `allow_with_flag` needs a cap | Cap required | The teams file for `serve` uses `deny` and no cap |
| D-015: budget period is the UTC calendar month | UTC month | `serve` reads `SystemClock`, which is UTC. No time-zone option is added |
| D-016: cache rules (key holds policy and catalog hash, per-team scope, only temperature `"0"`, lower-tier answers not stored, a hit is free) | As written | Section 5.2 and 5.3 are written so these rules behave the same over HTTP |
| D-022: build-log number and license | None stated; no `LICENSE` file | The README line names neither |
| D-038: AC-C2 wording (an opening balance above the hard limit) | Reading (b): AC-C2 holds when adjustments before the first reserve leave `spent <= hard_limit`. PRD not edited | X12 uses no opening balance, so it does not depend on the reading |
| Review item: retry-budget lists are never pruned | Was left alone in Phase 1 | **Fixed in Phase 2** (section 6), because a server is long-lived |
| Review item: WE-18's downgrade models and WE-27's re-store time are asserted only in unit tests, not in the case files | Stays as it is | Case files are frozen in Phase 2 (X2). WE-18's downgrade models are in the trace's `downgrade_chain` event, so X3's byte comparison covers them through HTTP. WE-27's re-store time is not in a trace and stays a unit test |
| Phase 1 "Not verified": engine never ran overlapping calls | | X4, X13 to X16 |
| Phase 1 "Not verified": no test uses `SystemClock` | | One unit test (section 7, `serve.py`) |
| PRD Q8: backoff jitter ("Phase 2 can add") | No jitter | Not added. The golden timestamps depend on a fixed backoff |
| PRD Q9: a deadline for the whole request ("a Phase 2 question") | No deadline | Not added. `max_attempts` and the per-attempt timeout bound a request |

## 13. Not in Phase 2

- Real providers, API keys, any outbound call, any deployment, any hosting, any account.
- Listening on anything but `127.0.0.1`. TLS. Authentication. Rate limiting.
- Streaming, tool calls, images, audio, `n` above 1.
- A real tokenizer. Semantic caching. Sharing one in-flight answer between identical requests.
- Resuming from existing ledger or trace files. `serve` starts fresh in an empty data directory (D-057).
- A database, more than one process, more than one host.
- Admin endpoints, HTML views, `switchyard policy diff`, `switchyard sweep` (Phase 3).
- The Yardstick gate itself and any CI step that runs Yardstick (PRD section 9 stays a design).
- Backoff jitter, a whole-request deadline, a `Retry-After` header, reloading config while running.
- Any change to a case file, the catalog or the policy. Any new worked example.
- Any load, throughput or latency measurement. No performance number is claimed.
- An async server. The demo script and the full README (Phase 4).

## 14. Risks and how to handle them

- **A golden case differs through HTTP.** Do not edit an expected value or the comparison. Find which side is
  wrong by diffing the two trace files line by line. If the mapping is wrong, fix the mapping. If a section 6
  change altered the engine, the Phase 1 suite should have caught it at the step 4 checkpoint; stop and report.
- **A Phase 1 test needs an edit.** Stop. That means a section 6 change is not behaviour-preserving. Report the
  test, the old value and the new value.
- **A gated test hangs or flakes.** A gate has a hard timeout that fails the test. A flaky gated test is a
  broken test or a real race in the engine. It is never retried, skipped or loosened.
- **X12 gives a count outside 10 to 13.** That is an overspend or a lost request, so it is a bug. Print the
  ledger and stop.
- **The pin does not resolve, or Werkzeug behaves differently from the spike.** Stop and report. Do not swap
  the library without a decision row.
- **Yardstick cannot be reached from the build run.** Write the target from the Yardstick repository if it can
  be read; otherwise leave X20 and X21 open and say so. Do not guess the target format and call it verified.
- **Scope creep.** No HTML, no admin route, no resume, no jitter. See section 13.

## 15. Pull request for Phase 2

Branch `phase-2` cut from `main`. One draft pull request titled "switchyard: Phase 2 HTTP gateway (fakes only)".
Body: what was built; the exit criteria table with pass, fail or open for each and the real output of the
gates; the list of test files changed and added; the HTTP contract in brief; new `DECISIONS.md` rows; reviewer
findings; "Not verified"; and "You:" steps. Then stop. Do not start Phase 3.
