from __future__ import annotations

from pathlib import Path

import pytest

from switchyard.cases import CaseError, load_case, run_case
from switchyard.config_io import ConfigError
from tests.helpers import FIXTURES

OUTAGE = (FIXTURES / "cases" / "c02-provider-outage.yaml").read_text()


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "case.yaml"
    path.write_text(
        text.replace("../catalog/", f"{FIXTURES}/catalog/").replace("../policy/", f"{FIXTURES}/policy/")
    )
    return path


def test_out_of_order_at_ms_is_rejected(tmp_path: Path) -> None:
    text = OUTAGE.replace("at_ms: 1000\n", "at_ms: 100\n", 1)
    with pytest.raises(CaseError, match="earlier than the previous request"):
        run_case(load_case(_write(tmp_path, text)))


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="surprise"):
        load_case(
            _write(tmp_path, OUTAGE.replace("illustrative: true\n", "illustrative: true\nsurprise: 1\n", 1))
        )


def test_float_money_in_a_case_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="hard_limit_micro"):
        load_case(
            _write(tmp_path, OUTAGE.replace("hard_limit_micro: 1000000000000", "hard_limit_micro: 1.0e12", 1))
        )


def test_reconcile_clean_is_required(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="reconcile"):
        load_case(_write(tmp_path, OUTAGE.replace("  reconcile: clean\n", "")))


def test_unknown_script_provider_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown provider"):
        load_case(_write(tmp_path, OUTAGE.replace("scripts:\n  alder:", "scripts:\n  oak:", 1)))


def test_wrong_expectation_is_reported_not_hidden(tmp_path: Path) -> None:
    result = run_case(load_case(_write(tmp_path, OUTAGE.replace("ledger_entries: 22", "ledger_entries: 23"))))
    assert result.mismatches == [("end", "end: ledger_entries: expected 23, got 22")]
