"""Generated engine runs for the invariant tests: random teams, budgets, outage scripts, requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hypothesis import strategies as st

from switchyard.engine import Engine
from switchyard.models import Trace
from switchyard.providers.fake import ScriptWindow
from tests.helpers import START_MS, engine, team

OUTCOMES = ["timeout", "server_error", "rate_limited", "bad_request", "ok"]
SHAPES = [
    ("support", "helpdesk", "triage", "classify"),
    ("support", "helpdesk", "reply-draft", "draft"),
    ("legal", "clm", "contract-review", "reason"),
    ("research", "nb", "notes", "summarize"),
    ("ghost", "x", "y", "z"),  # no team config
]


@dataclass
class Spec:
    teams: dict[str, Any]
    openings: dict[str, int]
    scripts: dict[str, list[ScriptWindow]]
    requests: list[tuple[int, dict[str, Any], int | None]]  # (gap_ms, raw request, fake_output_tokens)


@st.composite
def windows(draw: st.DrawFn) -> list[ScriptWindow]:
    out: list[ScriptWindow] = []
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        start = draw(st.integers(min_value=0, max_value=60_000))
        data: dict[str, Any] = {
            "from_ms": start,
            "to_ms": start + draw(st.integers(min_value=1, max_value=30_000)),
            "outcome": draw(st.sampled_from(OUTCOMES)),
            "latency_ms": draw(st.none() | st.integers(min_value=0, max_value=6_000)),
            "billed_input_tokens": draw(st.none() | st.integers(min_value=0, max_value=50)),
            "billed_output_tokens": draw(st.none() | st.integers(min_value=0, max_value=5)),
        }
        out.append(ScriptWindow.model_validate(data))
    return out


@st.composite
def team_configs(draw: st.DrawFn) -> dict[str, Any]:
    hard = draw(st.integers(min_value=0, max_value=60_000))
    soft = draw(st.integers(min_value=0, max_value=hard))
    overage = draw(st.sampled_from(["deny", "downgrade", "allow_with_flag"]))
    cap = draw(st.integers(min_value=1, max_value=20_000)) if overage == "allow_with_flag" else 0
    return {
        "soft": soft,
        "hard": hard,
        "overage": overage,
        "cap": cap,
        "cache_on": draw(st.booleans()),
        "ttl_ms": draw(st.integers(min_value=1, max_value=60_000)),
    }


@st.composite
def specs(draw: st.DrawFn, max_requests: int = 12) -> Spec:
    teams = {name: draw(team_configs()) for name in ("support", "legal", "research")}
    openings = {name: draw(st.integers(min_value=0, max_value=60_000)) for name in teams}
    scripts = {p: draw(windows()) for p in ("alder", "birch", "cedar")}
    requests = []
    for i in range(draw(st.integers(min_value=1, max_value=max_requests))):
        t, app, uc, tc = draw(st.sampled_from(SHAPES))
        raw = {
            "request_id": draw(st.sampled_from([f"r{i}", f"r{i}", f"r{i}", "r0"])),  # sometimes a duplicate
            "team": t,
            "app": app,
            "use_case": uc,
            "task_class": tc,
            "data_tags": draw(st.lists(st.sampled_from(["pii", "restricted"]), unique=True, max_size=2)),
            "messages": [
                {"role": "user", "content": draw(st.sampled_from(["Illustrative a", "Illustrative b "]))}
            ],
            "input_tokens": draw(st.integers(min_value=50, max_value=4_000)),
            "max_output_tokens": draw(st.integers(min_value=5, max_value=1_500)),
            "temperature": draw(st.sampled_from(["0", "0", "0.7"])),
        }
        fake = draw(st.none() | st.integers(min_value=0, max_value=2_000))
        requests.append((draw(st.integers(min_value=0, max_value=15_000)), raw, fake))
    return Spec(teams, openings, scripts, requests)


def run(spec: Spec) -> Engine:
    e = engine(teams={k: team(**v) for k, v in spec.teams.items()}, scripts=spec.scripts)
    for name, amount in sorted(spec.openings.items()):
        e.ledger.adjust(name, amount, START_MS, "illustrative opening balance")
    for gap, raw, fake in spec.requests:
        e.handle_raw(raw, arrival_ms=e.clock.now_ms() + gap, fake_output_tokens=fake)
    return e


def traces(e: Engine) -> list[Trace]:
    return list(e.traces)
