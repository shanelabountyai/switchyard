"""SW-02 / AC-C1 to AC-C3: reservations never take committed past the ceiling, under any interleaving."""

from __future__ import annotations

import threading

from hypothesis import given, settings
from hypothesis import strategies as st

from switchyard.ledger import Ledger, LedgerError
from switchyard.models import EntryKind, Overage, TeamBudget
from tests.helpers import START_MS, team
from tests.invariants.runs import Spec, run, specs

PERIOD = "2026-11"


@st.composite
def budgets(draw: st.DrawFn) -> TeamBudget:
    hard = draw(st.integers(min_value=0, max_value=5_000))
    overage = draw(st.sampled_from(["deny", "downgrade", "allow_with_flag"]))
    cap = draw(st.integers(min_value=1, max_value=2_000)) if overage == "allow_with_flag" else 0
    return team(soft=draw(st.integers(0, hard)), hard=hard, overage=overage, cap=cap).budget


ops = st.lists(
    st.tuples(
        st.sampled_from(["reserve", "reserve", "settle", "settle", "adjust"]),
        st.integers(min_value=0, max_value=19),  # one of up to 20 requests
        st.integers(min_value=0, max_value=1_500),
        st.integers(min_value=-2_000, max_value=2_000),
    ),
    max_size=80,
)


def _recompute(ledger: Ledger) -> tuple[int, int]:
    entries = ledger.entries()
    spent = sum(e.amount_micro for e in entries if e.kind in (EntryKind.SETTLE, EntryKind.ADJUSTMENT))
    closed = {e.ref_seq for e in entries if e.ref_seq is not None}
    held = sum(e.amount_micro for e in entries if e.kind is EntryKind.RESERVE and e.seq not in closed)
    return spent, held


@settings(max_examples=300, deadline=None)
@given(budgets(), ops, st.integers(min_value=0, max_value=6_000))
def test_reservations_never_exceed_the_ceiling_under_any_interleaving(
    budget: TeamBudget, steps: list[tuple[str, int, int, int]], opening: int
) -> None:
    ledger = Ledger()
    ledger.adjust("t", opening, START_MS, "opening")
    attempts: dict[int, int] = {}
    adjusted_after_first_reserve = False
    reserved_any = False
    pre_reserve_over_hard = opening > budget.hard_limit_micro  # AC-C2 precondition (D-038)
    for i, (op, req, amount, signed) in enumerate(steps):
        now = START_MS + i
        before = ledger.balance("t", PERIOD)
        if op == "reserve":
            attempts[req] = attempts.get(req, 0) + 1
            result = ledger.reserve("t", f"q{req}", attempts[req], amount, now, budget)
            after = ledger.balance("t", PERIOD)
            if result.accepted:
                reserved_any = True
                assert after.committed <= budget.ceiling  # AC-C1: every accepted reserve
                assert after.committed == before.committed + amount
            else:
                assert before.committed + amount > budget.ceiling
                assert after == before  # a refusal writes nothing
        elif op == "settle":
            open_holds = [e for e in ledger.open_reserves() if e.request_id == f"q{req}"]
            if not open_holds:
                continue
            hold = open_holds[0]
            assert hold.request_id is not None and hold.attempt is not None
            ledger.settle(hold.request_id, hold.attempt, min(amount, hold.amount_micro), now)
            assert (
                ledger.balance("t", PERIOD).committed <= before.committed
            )  # a settle never raises committed
        else:
            try:
                ledger.adjust("t", signed, now, "illustrative correction")
            except LedgerError:
                assert before.spent + signed < 0
                continue
            adjusted_after_first_reserve = adjusted_after_first_reserve or reserved_any
            if not reserved_any and ledger.balance("t", PERIOD).spent > budget.hard_limit_micro:
                pre_reserve_over_hard = True
        balance = ledger.balance("t", PERIOD)
        assert (balance.spent, balance.held) == _recompute(ledger)
        if (
            budget.overage in (Overage.DENY, Overage.DOWNGRADE)
            and not adjusted_after_first_reserve
            and not pre_reserve_over_hard
        ):
            assert balance.spent <= budget.hard_limit_micro  # AC-C2


@settings(max_examples=60, deadline=None)
@given(specs())
def test_engine_runs_never_overspend(spec: Spec) -> None:
    """Through the engine: every reserve left committed at or below the ceiling for its team and period."""
    e = run(spec)
    running: dict[tuple[str, str], int] = {}
    holds: dict[int, int] = {}
    for entry in e.ledger.entries():
        key = (entry.team, entry.period)
        if entry.kind is EntryKind.RESERVE:
            running[key] = running.get(key, 0) + entry.amount_micro
            holds[entry.seq] = entry.amount_micro
            assert running[key] <= e.teams[entry.team].budget.ceiling
        elif entry.kind in (EntryKind.SETTLE, EntryKind.RELEASE):
            assert entry.ref_seq is not None
            running[key] = running.get(key, 0) - holds[entry.ref_seq] + entry.amount_micro
        else:
            running[key] = running.get(key, 0) + entry.amount_micro


def test_threaded_reserves_fit_exactly_ten() -> None:
    """AC-C3: 32 threads race for a budget that fits 10 reserves; exactly 10 win on every one of 50 runs."""
    budget = team(soft=1_200, hard=1_200).budget
    for run_number in range(50):
        ledger = Ledger()
        barrier = threading.Barrier(32)
        accepted: list[bool] = []
        lock = threading.Lock()

        def worker(
            n: int,
            ledger: Ledger = ledger,
            barrier: threading.Barrier = barrier,
            lock: threading.Lock = lock,
            accepted: list[bool] = accepted,
            run_number: int = run_number,
        ) -> None:
            barrier.wait()
            result = ledger.reserve("t", f"run{run_number}-{n}", 1, 120, START_MS, budget)
            with lock:
                accepted.append(result.accepted)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(32)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(accepted) == 10
        balance = ledger.balance("t", PERIOD)
        assert (balance.held, balance.committed) == (1_200, 1_200)
