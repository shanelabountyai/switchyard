from __future__ import annotations

import pytest

from switchyard.models import Outcome
from switchyard.providers.fake import FakeProvider, ScriptError, ScriptWindow
from tests.helpers import catalog, request, window

SMALL = catalog().model("alder-small")


def _call(
    script: list[ScriptWindow], started: int, timeout: int = 2000, out: int | None = 12
) -> tuple[str, int]:
    result = FakeProvider("alder", script, 0).call(SMALL, request(), started, timeout, out, "abc")
    return result.outcome.value, result.latency_ms


def test_window_edges() -> None:
    script = [window(100, 200, "server_error", latency_ms=50)]
    assert _call(script, 99) == ("ok", 400)
    assert _call(script, 100) == ("server_error", 50)
    assert _call(script, 199) == ("server_error", 50)
    assert _call(script, 200) == ("ok", 400)


def test_first_matching_window_wins() -> None:
    script = [window(0, 100, "timeout"), window(0, 100, "rate_limited")]
    assert _call(script, 0, timeout=5000)[0] == "timeout"


def test_latency_over_timeout_becomes_timeout_after_exactly_timeout() -> None:
    assert _call([window(0, 10, "ok", latency_ms=9999)], 0, timeout=2000) == ("timeout", 2000)
    assert _call([window(0, 10, "server_error", latency_ms=2001)], 0, timeout=2000) == ("timeout", 2000)
    assert _call([window(0, 10, "ok", latency_ms=2000)], 0, timeout=2000) == ("ok", 2000)


def test_ok_bills_request_input_and_capped_output() -> None:
    provider = FakeProvider("alder", [], 0)
    result = provider.call(SMALL, request(), 0, 2000, 999, "abc")
    assert (result.input_tokens, result.output_tokens) == (400, 50)
    assert result.text == "[fake alder-small] abc"
    assert provider.call(SMALL, request(), 0, 2000, None, "x").output_tokens == 50


def test_failure_bills_only_scripted_tokens() -> None:
    provider = FakeProvider(
        "alder", [window(0, 10, "timeout", billed_input_tokens=400, billed_output_tokens=3)], 0
    )
    result = provider.call(SMALL, request(), 0, 2000, 12, "x")
    assert (result.outcome, result.input_tokens, result.output_tokens) == (Outcome.TIMEOUT, 400, 3)
    assert (
        FakeProvider("alder", [window(0, 10, "server_error")], 0)
        .call(SMALL, request(), 0, 2000, 12, "x")
        .input_tokens
        == 0
    )


def test_script_may_not_bill_more_than_the_request() -> None:
    provider = FakeProvider("alder", [window(0, 10, "server_error", billed_output_tokens=51)], 0)
    with pytest.raises(ScriptError):
        provider.call(SMALL, request(), 0, 2000, 12, "x")


def test_window_validation() -> None:
    with pytest.raises(ValueError):
        window(10, 10, "ok")
    with pytest.raises(ValueError):
        window(0, 10, "exploded")


def test_wrong_provider_rejected() -> None:
    with pytest.raises(ScriptError):
        FakeProvider("birch", [], 0).call(SMALL, request(), 0, 2000, 12, "x")


def test_bad_request_never_bills() -> None:
    provider = FakeProvider("alder", [window(0, 10, "bad_request", billed_input_tokens=400)], 0)
    result = provider.call(SMALL, request(), 0, 2000, 12, "x")
    assert (result.outcome, result.input_tokens, result.output_tokens) == (Outcome.BAD_REQUEST, 0, 0)
