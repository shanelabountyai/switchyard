from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from switchyard.cli import app
from tests.helpers import FIXTURES, ROOT

runner = CliRunner()
OUTAGE = str(FIXTURES / "cases" / "c02-provider-outage.yaml")


def test_replay_ok_and_writes_jsonl(tmp_path: Path) -> None:
    traces, ledger = tmp_path / "t.jsonl", tmp_path / "l.jsonl"
    result = runner.invoke(app, ["replay", OUTAGE, "--traces", str(traces), "--ledger", str(ledger)])
    assert result.exit_code == 0, result.output
    assert "all expectations matched" in result.output
    assert len(traces.read_text().splitlines()) == 6
    assert len(ledger.read_text().splitlines()) == 22


def test_replay_mismatch_exits_1(tmp_path: Path) -> None:
    text = Path(OUTAGE).read_text().replace("ledger_entries: 22", "ledger_entries: 21")
    text = text.replace("../catalog/", f"{FIXTURES}/catalog/").replace("../policy/", f"{FIXTURES}/policy/")
    case = tmp_path / "c.yaml"
    case.write_text(text)
    result = runner.invoke(app, ["replay", str(case)])
    assert result.exit_code == 1
    assert "MISMATCH" in result.output


def test_bad_input_exits_2(tmp_path: Path) -> None:
    assert runner.invoke(app, ["replay", str(tmp_path / "missing.yaml")]).exit_code == 2
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\n")
    assert runner.invoke(app, ["report", str(bad)]).exit_code == 2
    assert runner.invoke(app, ["explain", OUTAGE, "nope"]).exit_code == 2


def test_explain_prints_reasons_and_events() -> None:
    result = runner.invoke(app, ["explain", OUTAGE, "r5"])
    assert result.exit_code == 0, result.output
    for needle in (
        "rule_matched support-triage",
        "probe alder",
        "breaker_opened alder",
        "no_retry alder-small",
    ):
        assert needle in result.output


def test_report_matches_committed_example(tmp_path: Path) -> None:
    out = tmp_path / "r.md"
    result = runner.invoke(app, ["report", OUTAGE, "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text() == (ROOT / "examples" / "provider-outage-report.md").read_text()
    as_json = runner.invoke(app, ["report", OUTAGE, "--json"])
    assert as_json.exit_code == 0 and as_json.output.startswith("[{")
