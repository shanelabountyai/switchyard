"""The append-only budget ledger (PRD 6.1, 6.2). Holds state; one lock.

``reserve`` checks the budget and appends under the same lock, counting ``held``, so two requests in flight
cannot both take the last of a budget. No public method edits or removes an entry.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass

from switchyard.budget import check
from switchyard.canonical import canonical_json
from switchyard.clock import period_of
from switchyard.models import UNSETTLED, EntryKind, LedgerEntry, TeamBudget
from switchyard.money import require_int


class LedgerError(ValueError):
    """An operation that would break ledger integrity. Nothing is appended."""


@dataclass(frozen=True)
class Balance:
    spent: int
    held: int

    @property
    def committed(self) -> int:
        return self.spent + self.held


@dataclass(frozen=True)
class ReserveResult:
    accepted: bool
    entry: LedgerEntry | None
    flags: tuple[str, ...]
    amount: int
    before: Balance
    ceiling: int


class Ledger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[LedgerEntry] = []
        self._spent: dict[tuple[str, str], int] = {}
        self._held: dict[tuple[str, str], int] = {}
        self._open: dict[tuple[str, int], LedgerEntry] = {}
        self._reserved_keys: set[tuple[str, int]] = set()

    # --- reads ---------------------------------------------------------------------------------------------

    def entries(self) -> tuple[LedgerEntry, ...]:
        with self._lock:
            return tuple(self._entries)

    def balance(self, team: str, period: str) -> Balance:
        with self._lock:
            return self._balance(team, period)

    def open_reserves(self) -> tuple[LedgerEntry, ...]:
        with self._lock:
            return tuple(sorted(self._open.values(), key=lambda e: e.seq))

    def _balance(self, team: str, period: str) -> Balance:
        key = (team, period)
        return Balance(spent=self._spent.get(key, 0), held=self._held.get(key, 0))

    # --- writes --------------------------------------------------------------------------------------------

    def _append(self, entry: LedgerEntry) -> LedgerEntry:
        key = (entry.team, entry.period)
        if entry.kind is EntryKind.RESERVE:
            assert entry.request_id is not None and entry.attempt is not None
            self._held[key] = self._held.get(key, 0) + entry.amount_micro
            self._open[(entry.request_id, entry.attempt)] = entry
            self._reserved_keys.add((entry.request_id, entry.attempt))
        elif entry.kind in (EntryKind.SETTLE, EntryKind.RELEASE):
            assert entry.request_id is not None and entry.attempt is not None
            reserve = self._open.pop((entry.request_id, entry.attempt))
            self._held[key] = self._held.get(key, 0) - reserve.amount_micro
            self._spent[key] = self._spent.get(key, 0) + entry.amount_micro
        else:
            self._spent[key] = self._spent.get(key, 0) + entry.amount_micro
        self._entries.append(entry)
        return entry

    def _next_seq(self) -> int:
        return len(self._entries) + 1

    def reserve(
        self,
        team: str,
        request_id: str,
        attempt: int,
        amount: int,
        now: int,
        budget: TeamBudget,
    ) -> ReserveResult:
        """Hold ``amount`` for one attempt in the period containing ``now``, or refuse (writes nothing)."""
        require_int("amount", amount)
        require_int("attempt", attempt)
        if amount < 0:
            raise LedgerError(f"reserve amount must be >= 0, got {amount}")
        period = period_of(now)
        with self._lock:
            if (request_id, attempt) in self._reserved_keys:
                raise LedgerError(f"duplicate reserve for request {request_id!r} attempt {attempt}")
            before = self._balance(team, period)
            decision = check(before.committed, amount, budget)
            if not decision.accepted:
                return ReserveResult(False, None, (), amount, before, decision.ceiling)
            entry = LedgerEntry(
                seq=self._next_seq(),
                ts_ms=now,
                team=team,
                period=period,
                kind=EntryKind.RESERVE,
                request_id=request_id,
                attempt=attempt,
                amount_micro=amount,
                flags=decision.flags,
            )
            self._append(entry)
            return ReserveResult(True, entry, decision.flags, amount, before, decision.ceiling)

    def _close(
        self,
        kind: EntryKind,
        request_id: str,
        attempt: int,
        amount: int,
        now: int,
        memo: str,
        flags: tuple[str, ...],
    ) -> LedgerEntry:
        require_int("amount", amount)
        with self._lock:
            reserve = self._open.get((request_id, attempt))
            if reserve is None:
                if (request_id, attempt) in self._reserved_keys:
                    raise LedgerError(f"reserve for {request_id!r} attempt {attempt} is already closed")
                raise LedgerError(f"no reserve for {request_id!r} attempt {attempt}")
            if amount < 0:
                raise LedgerError(f"{kind} amount must be >= 0, got {amount}")
            if amount > reserve.amount_micro:
                raise LedgerError(f"settle {amount} exceeds its reserve {reserve.amount_micro}")
            entry = LedgerEntry(
                seq=self._next_seq(),
                ts_ms=now,
                team=reserve.team,
                period=reserve.period,  # posted to the period of the reserve it closes
                kind=kind,
                request_id=request_id,
                attempt=attempt,
                amount_micro=amount,
                ref_seq=reserve.seq,
                flags=flags,
                memo=memo,
            )
            return self._append(entry)

    def settle(self, request_id: str, attempt: int, actual: int, now: int) -> LedgerEntry:
        """Close a reserve with the actual cost. ``actual`` may not exceed the reserve."""
        return self._close(EntryKind.SETTLE, request_id, attempt, actual, now, "", ())

    def release(
        self, request_id: str, attempt: int, now: int, memo: str, flags: tuple[str, ...] = ()
    ) -> LedgerEntry:
        """Close a reserve with no spend."""
        if not memo:
            raise LedgerError("release requires a memo")
        return self._close(EntryKind.RELEASE, request_id, attempt, 0, now, memo, flags)

    def adjust(self, team: str, amount: int, now: int, memo: str) -> LedgerEntry:
        """A signed manual correction or opening balance, posted to the period of ``now``. No limit check."""
        require_int("amount", amount)
        if not memo:
            raise LedgerError("adjustment requires a memo")
        period = period_of(now)
        with self._lock:
            before = self._balance(team, period)
            if before.spent + amount < 0:
                raise LedgerError(f"adjustment {amount} would take spent below 0 ({before.spent})")
            entry = LedgerEntry(
                seq=self._next_seq(),
                ts_ms=now,
                team=team,
                period=period,
                kind=EntryKind.ADJUSTMENT,
                amount_micro=amount,
                memo=memo,
            )
            return self._append(entry)

    def sweep(self, now: int, ttl_ms: int) -> list[LedgerEntry]:
        """Release every reserve at least ``ttl_ms`` old, flagged ``unsettled`` for a person to check."""
        stale = [e for e in self.open_reserves() if now - e.ts_ms >= ttl_ms]
        released: list[LedgerEntry] = []
        for reserve in stale:
            assert reserve.request_id is not None and reserve.attempt is not None
            released.append(
                self.release(
                    reserve.request_id,
                    reserve.attempt,
                    now,
                    memo=f"swept: unsettled after {ttl_ms} ms; check whether the provider billed",
                    flags=(UNSETTLED,),
                )
            )
        return released

    # --- JSON Lines ----------------------------------------------------------------------------------------

    def to_jsonl(self) -> str:
        return "".join(canonical_json(e.model_dump(mode="json")) + "\n" for e in self.entries())

    @classmethod
    def from_entries(cls, entries: Iterable[LedgerEntry]) -> Ledger:
        """Rebuild a ledger, checking ``seq`` and that each close matches an open reserve."""
        ledger = cls()
        for entry in entries:
            if entry.seq != ledger._next_seq():
                raise LedgerError(f"seq {entry.seq} out of order (expected {ledger._next_seq()})")
            if entry.kind is EntryKind.RESERVE:
                assert entry.request_id is not None and entry.attempt is not None
                if (entry.request_id, entry.attempt) in ledger._reserved_keys:
                    raise LedgerError(f"duplicate reserve at seq {entry.seq}")
            elif entry.kind in (EntryKind.SETTLE, EntryKind.RELEASE):
                assert entry.request_id is not None and entry.attempt is not None
                reserve = ledger._open.get((entry.request_id, entry.attempt))
                if reserve is None or reserve.seq != entry.ref_seq:
                    raise LedgerError(f"seq {entry.seq} closes no open reserve")
                if entry.amount_micro > reserve.amount_micro:
                    raise LedgerError(f"seq {entry.seq} settles more than its reserve")
            ledger._append(entry)
        return ledger

    @classmethod
    def from_jsonl(cls, text: str) -> Ledger:
        return cls.from_entries(LedgerEntry.model_validate_json(line) for line in text.splitlines() if line)
