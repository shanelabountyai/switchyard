"""A fake provider with scripted latency, errors and billed tokens. No randomness, no network.

The fake does not move the clock; the engine advances it by the returned ``latency_ms``.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import model_validator

from switchyard.models import Frozen, ModelSpec, NonNegInt, Outcome, Request
from switchyard.providers.base import ProviderResult


class ScriptError(ValueError):
    """A script window is invalid, or bills more than the request allows."""


class ScriptWindow(Frozen):
    """A call that starts in ``[from_ms, to_ms)`` (relative to the case start) follows this window."""

    from_ms: NonNegInt
    to_ms: NonNegInt
    outcome: Outcome
    latency_ms: NonNegInt | None = None
    billed_input_tokens: NonNegInt | None = None
    billed_output_tokens: NonNegInt | None = None

    @model_validator(mode="after")
    def _check(self) -> ScriptWindow:
        if self.to_ms <= self.from_ms:
            raise ValueError("to_ms must be greater than from_ms")
        return self


class FakeProvider:
    def __init__(self, provider_id: str, script: Sequence[ScriptWindow], case_start_ms: int) -> None:
        self.provider_id = provider_id
        self._script = tuple(script)
        self._case_start_ms = case_start_ms

    def _window(self, started_ms: int) -> ScriptWindow | None:
        relative = started_ms - self._case_start_ms
        for window in self._script:
            if window.from_ms <= relative < window.to_ms:
                return window
        return None

    def call(
        self,
        model: ModelSpec,
        request: Request,
        started_ms: int,
        timeout_ms: int,
        fake_output_tokens: int | None,
        tag: str,
    ) -> ProviderResult:
        if model.provider != self.provider_id:
            raise ScriptError(f"model {model.id} does not belong to provider {self.provider_id}")
        window = self._window(started_ms)
        outcome = window.outcome if window is not None else Outcome.OK
        latency = model.declared_latency_ms
        if window is not None and window.latency_ms is not None:
            latency = window.latency_ms
        if latency > timeout_ms:
            outcome, latency = Outcome.TIMEOUT, timeout_ms
        if outcome is Outcome.OK:
            wanted = request.max_output_tokens if fake_output_tokens is None else fake_output_tokens
            return ProviderResult(
                outcome=outcome,
                latency_ms=latency,
                input_tokens=request.input_tokens,
                output_tokens=min(request.max_output_tokens, wanted),
                text=f"[fake {model.id}] {tag}",
            )
        # Billed tokens apply only when the final outcome is a failure (PRD 5.1); bad_request bills nothing.
        billable = window is not None and outcome.is_failure
        billed_in = (window.billed_input_tokens or 0) if billable and window is not None else 0
        billed_out = (window.billed_output_tokens or 0) if billable and window is not None else 0
        if billed_in > request.input_tokens or billed_out > request.max_output_tokens:
            raise ScriptError(
                f"script bills {billed_in} in / {billed_out} out, more than request "
                f"{request.request_id} allows "
                f"({request.input_tokens} in / {request.max_output_tokens} out)"
            )
        return ProviderResult(
            outcome=outcome, latency_ms=latency, input_tokens=billed_in, output_tokens=billed_out, text=""
        )
