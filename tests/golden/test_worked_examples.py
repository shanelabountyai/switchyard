"""Every worked example in PRD section 11 is asserted against its case file (fixtures/cases)."""

from __future__ import annotations

from pathlib import Path

import pytest

from switchyard.cases import InterleavedCase, load_case
from tests.helpers import CASES, case_result

WORKED_EXAMPLES = [f"WE-{i:02d}" for i in range(1, 31)]


def _case_for(we: str) -> Path:
    owners = [p for p in CASES if we in load_case(p).case.covers]
    assert len(owners) == 1, f"{we} is covered by {len(owners)} cases"
    return owners[0]


def test_fifteen_cases() -> None:
    assert len(CASES) == 15


def test_each_worked_example_is_covered_by_exactly_one_case() -> None:
    covers = [we for p in CASES for we in load_case(p).case.covers]
    assert sorted(covers) == WORKED_EXAMPLES


def test_request_labels_are_in_their_case_covers() -> None:
    for path in CASES:
        case = load_case(path).case
        labels = {case.covers[0]} if isinstance(case, InterleavedCase) else {r.we for r in case.requests}
        assert labels <= set(case.covers), path.stem


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_case_matches_every_expectation(path: Path) -> None:
    assert case_result(path).mismatches == []


@pytest.mark.parametrize("we", WORKED_EXAMPLES)
def test_worked_example(we: str) -> None:
    result = case_result(_case_for(we))
    assert result.mismatches_for(we) == []
