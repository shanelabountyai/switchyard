"""Exact-match response cache, scoped to one team (PRD 7.1). Holds state; one lock."""

from __future__ import annotations

import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from switchyard.canonical import content_hash
from switchyard.models import CacheStatus, Message, Request, TeamCache

CACHE_KEY_VERSION = 1


def normalize_content(content: str) -> str:
    """NFC, ``\\r\\n`` and ``\\r`` to ``\\n``, strip leading and trailing whitespace. Nothing else."""
    text = unicodedata.normalize("NFC", content)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def normalize(messages: Sequence[Message]) -> list[dict[str, str]]:
    return [{"role": m.role.lower(), "content": normalize_content(m.content)} for m in messages]


def cache_key(
    request: Request,
    rule_id: str,
    max_latency_ms: int,
    max_cost_micro: int,
    config_version: str,
) -> str:
    """sha256 of the canonical JSON of the key fields. ``app``, ``request_id`` and times are not in it."""
    return content_hash(
        {
            "v": CACHE_KEY_VERSION,
            "team": request.team,
            "rule_id": rule_id,
            "use_case": request.use_case,
            "task_class": request.task_class,
            "messages": normalize(request.messages),
            "input_tokens": request.input_tokens,
            "max_output_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "max_latency_ms": max_latency_ms,
            "max_cost_micro": max_cost_micro,
            "config_version": config_version,
        }
    )


def bypass_reason(request: Request, team_cache: TeamCache) -> CacheStatus | None:
    """First match wins: sensitive, team opt-out, non-deterministic. ``None`` means use the cache."""
    if request.data_tags:
        return CacheStatus.BYPASS_SENSITIVE
    if not team_cache.enabled:
        return CacheStatus.BYPASS_TEAM_OPT_OUT
    if request.temperature != "0":
        return CacheStatus.BYPASS_NONDETERMINISTIC
    return None


@dataclass(frozen=True)
class CacheEntry:
    stored_at: int
    model: str
    cost_micro: int
    text: str


class CacheRefused(ValueError):
    """A ``put`` for a request that must never be cached."""


class ExactCache:
    def __init__(self, max_entries: int) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max = max_entries
        self._lock = threading.Lock()
        self._teams: dict[str, dict[str, CacheEntry]] = {}

    def get(self, team: str, key: str, now: int, ttl_ms: int) -> CacheEntry | None:
        """A hit while ``now - stored_at < ttl_ms``. An expired entry is dropped."""
        with self._lock:
            entries = self._teams.get(team, {})
            entry = entries.get(key)
            if entry is None:
                return None
            if now - entry.stored_at < ttl_ms:
                return entry
            del entries[key]
            return None

    def put(self, team: str, key: str, entry: CacheEntry, request: Request, team_cache: TeamCache) -> None:
        """Store an entry. Refuses a request the bypass rules exclude (a second guard behind the engine)."""
        reason = bypass_reason(request, team_cache)
        if reason is not None:
            raise CacheRefused(f"request {request.request_id} must not be cached ({reason})")
        with self._lock:
            entries = self._teams.setdefault(team, {})
            entries.pop(key, None)
            while len(entries) >= self._max:
                oldest = min(entries.items(), key=lambda kv: (kv[1].stored_at, kv[0]))
                del entries[oldest[0]]
            entries[key] = entry

    def size(self, team: str) -> int:
        with self._lock:
            return len(self._teams.get(team, {}))

    def sizes(self) -> dict[str, int]:
        with self._lock:
            return {team: len(entries) for team, entries in sorted(self._teams.items())}
