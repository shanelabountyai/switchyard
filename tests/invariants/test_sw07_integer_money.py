"""SW-07: no float in a money path. Money fields reject floats; src/ has no float(), float literal or '/'."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import BaseModel

from switchyard.models import Attempt, LedgerEntry, ModelSpec, ReportRow, TeamBudget, Trace
from tests.helpers import ROOT

SRC = ROOT / "src" / "switchyard"


def _violations(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.append(f"{path.name}:{node.lineno} float literal")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
            found.append(f"{path.name}:{node.lineno} float() call")
        if isinstance(node, ast.BinOp | ast.AugAssign) and isinstance(node.op, ast.Div):
            found.append(f"{path.name}:{node.lineno} true division")
    return found


def test_source_has_no_float_paths() -> None:
    files = sorted(SRC.rglob("*.py"))
    assert len(files) >= 15
    assert [v for f in files for v in _violations(f)] == []


def test_scanner_catches_each_kind(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text("a = 0.5\nb = float(1)\nc = 3 / 2\nc /= 2\n")
    assert len(_violations(sample)) == 4


@pytest.mark.parametrize("bad", [1.0, True, "120"])
@pytest.mark.parametrize(
    ("model", "base", "field"),
    [
        (
            ModelSpec,
            {
                "id": "m",
                "provider": "p",
                "tier": "economy",
                "input_price_micro_per_1m": 1,
                "output_price_micro_per_1m": 1,
                "declared_latency_ms": 1,
            },
            "input_price_micro_per_1m",
        ),
        (
            TeamBudget,
            {
                "period": "calendar_month_utc",
                "soft_limit_micro": 1,
                "hard_limit_micro": 2,
                "overage": "deny",
                "overage_cap_micro": 0,
            },
            "hard_limit_micro",
        ),
        (
            LedgerEntry,
            {
                "seq": 1,
                "ts_ms": 0,
                "team": "t",
                "period": "2026-11",
                "kind": "adjustment",
                "amount_micro": 1,
                "memo": "m",
            },
            "amount_micro",
        ),
    ],
)
def test_money_fields_reject_non_ints(
    model: type[BaseModel], base: dict[str, object], field: str, bad: object
) -> None:
    model.model_validate(base)
    with pytest.raises(ValueError):
        model.model_validate({**base, field: bad})


def test_trace_and_report_money_fields_are_strict() -> None:
    for model, fields in (
        (Attempt, ("reserved_micro", "cost_micro")),
        (Trace, ("cost_micro", "avoided_micro")),
        (ReportRow, ("cost_micro", "spent_micro")),
    ):
        for name in fields:
            metadata = model.model_fields[name].metadata
            assert any(type(m).__name__ == "Strict" for m in metadata), (model.__name__, name)
