"""The provider interface the engine calls. Phase 1 has only the fake implementation."""

from __future__ import annotations

from typing import Protocol

from switchyard.models import Frozen, ModelSpec, NonNegInt, Outcome, Request


class ProviderResult(Frozen):
    outcome: Outcome
    latency_ms: NonNegInt
    input_tokens: NonNegInt  # billed
    output_tokens: NonNegInt  # billed
    text: str


class Provider(Protocol):
    def call(
        self,
        model: ModelSpec,
        request: Request,
        started_ms: int,
        timeout_ms: int,
        fake_output_tokens: int | None,
        tag: str,
    ) -> ProviderResult: ...
