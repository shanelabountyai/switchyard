"""Generated engine runs for the invariant tests: random teams, budgets, outage scripts, requests.

Ranges are chosen so most requests route and most reserves fit, so the breaker, retry, fallback, budget and
cache paths are exercised (``tests/invariants/test_generators_reach.py`` proves each is reachable).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hypothesis import strategies as st

from switchyard.engine import Engine
from switchyard.providers.fake import ScriptWindow
from tests.helpers import START_MS, engine, team

FAILURES = ["timeout", "server_error", "rate_limited"]
OUTCOMES = [*FAILURES, *FAILURES, "bad_request", "ok"]
SHAPES = [
    ("support", "helpdesk", "triage", "classify"),
    ("support", "helpdesk", "triage", "classify"),
    ("support", "web", "notes", "summarize"),  # default rule
    ("research", "nb", "notes", "summarize"),
    ("research", "nb", "notes", "summarize"),
    ("support", "helpdesk", "reply-draft", "draft"),
    ("legal", "clm", "contract-review", "reason"),
    ("ghost", "x", "y", "z"),  # no team config
]


@dataclass
class Spec:
    teams: dict[str, Any]
    openings: dict[str, int]
    scripts: dict[str, list[ScriptWindow]]
    requests: list[tuple[int, dict[str, Any], int | None]]  # (gap_ms, raw request, fake_output_tokens)


def _window(data: dict[str, Any]) -> ScriptWindow:
    return ScriptWindow.model_validate(data)


@st.composite
def windows(draw: st.DrawFn, forced_outage: bool = False) -> list[ScriptWindow]:
    out: list[ScriptWindow] = []
    if forced_outage:
        out.append(
            _window(
                {
                    "from_ms": 0,
                    "to_ms": draw(st.integers(min_value=10_000, max_value=60_000)),
                    "outcome": draw(st.sampled_from(FAILURES)),
                    "latency_ms": draw(st.integers(min_value=1, max_value=300)),
                }
            )
        )
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        start = draw(st.integers(min_value=0, max_value=40_000))
        out.append(
            _window(
                {
                    "from_ms": start,
                    "to_ms": start + draw(st.integers(min_value=1, max_value=20_000)),
                    "outcome": draw(st.sampled_from(OUTCOMES)),
                    "latency_ms": draw(st.none() | st.integers(min_value=0, max_value=3_000)),
                    "billed_input_tokens": draw(st.none() | st.integers(min_value=0, max_value=50)),
                    "billed_output_tokens": draw(st.none() | st.integers(min_value=0, max_value=5)),
                }
            )
        )
    return out


@st.composite
def team_configs(draw: st.DrawFn) -> dict[str, Any]:
    hard = draw(st.sampled_from([0, 500, 2_000, 5_000, 20_000, 1_000_000]) | st.integers(0, 20_000))
    soft = draw(st.integers(min_value=0, max_value=hard))
    overage = draw(st.sampled_from(["deny", "downgrade", "allow_with_flag"]))
    cap = draw(st.integers(min_value=1, max_value=5_000)) if overage == "allow_with_flag" else 0
    return {
        "soft": soft,
        "hard": hard,
        "overage": overage,
        "cap": cap,
        "cache_on": draw(st.booleans()),
        "ttl_ms": draw(st.integers(min_value=1, max_value=60_000)),
    }


@st.composite
def raw_requests(draw: st.DrawFn, index: int) -> dict[str, Any]:
    t, app, uc, tc = draw(st.sampled_from(SHAPES))
    raw: dict[str, Any] = {
        "request_id": draw(st.sampled_from([f"r{index}"] * 6 + ["r0"])),  # sometimes a duplicate
        "team": t,
        "app": app,
        "use_case": uc,
        "task_class": tc,
        "data_tags": draw(
            st.lists(st.sampled_from(["pii", "pii", "restricted", "secret"]), unique=True, max_size=2)
            | st.just([])
        ),
        "messages": [
            {"role": "user", "content": draw(st.sampled_from(["Illustrative a", "Illustrative b "]))}
        ],
        # common values repeat, so identical requests (cache hits) and near-limit budgets happen often
        "input_tokens": draw(st.sampled_from([100, 400, 800]) | st.integers(min_value=50, max_value=800)),
        "max_output_tokens": draw(st.sampled_from([50, 100]) | st.integers(min_value=5, max_value=200)),
        "temperature": draw(st.sampled_from(["0", "0", "0", "0.7"])),
    }
    malformed = draw(st.sampled_from([None] * 12 + ["team", "messages", "input_tokens"]))
    if malformed is not None:
        del raw[malformed]
    return raw


@st.composite
def specs(draw: st.DrawFn, max_requests: int = 12, outage: bool = False) -> Spec:
    teams = {name: draw(team_configs()) for name in ("support", "legal", "research")}
    openings = {name: draw(st.sampled_from([0, 0, 100]) | st.integers(0, 3_000)) for name in teams}
    outage_provider = draw(st.sampled_from(["alder", "birch"])) if outage else None
    scripts = {p: draw(windows(forced_outage=p == outage_provider)) for p in ("alder", "birch", "cedar")}
    requests = []
    for i in range(draw(st.integers(min_value=1, max_value=max_requests))):
        raw = draw(raw_requests(i))
        fake = draw(st.none() | st.integers(min_value=0, max_value=200))
        gap = draw(st.integers(min_value=0, max_value=4_000) | st.integers(min_value=9_000, max_value=12_000))
        requests.append((gap, raw, fake))
    return Spec(teams, openings, scripts, requests)


def run(spec: Spec) -> Engine:
    e = engine(teams={k: team(**v) for k, v in spec.teams.items()}, scripts=spec.scripts)
    for name, amount in sorted(spec.openings.items()):
        e.ledger.adjust(name, amount, START_MS, "illustrative opening balance")
    for gap, raw, fake in spec.requests:
        e.handle_raw(raw, arrival_ms=e.clock.now_ms() + gap, fake_output_tokens=fake)
    return e
