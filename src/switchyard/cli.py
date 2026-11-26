"""``switchyard replay | explain | report`` over a golden case file. No HTTP server. Exit 2 on bad input."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from switchyard.cases import CaseError, CaseResult, load_case, run_case
from switchyard.clock import format_utc
from switchyard.config_io import ConfigError
from switchyard.money import format_usd
from switchyard.report import render_json, render_markdown
from switchyard.trace import to_jsonl

app = typer.Typer(
    help="Switchyard engines over golden case files. Fake providers and illustrative prices only.",
    no_args_is_help=True,
    add_completion=False,
)

CasePath = Annotated[Path, typer.Argument(help="A case file under fixtures/cases/.")]


def _run(case: Path) -> CaseResult:
    try:
        return run_case(load_case(case))
    except (ConfigError, CaseError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None


@app.command()
def replay(
    case: CasePath,
    traces: Annotated[Path | None, typer.Option(help="Write traces as JSON Lines here.")] = None,
    ledger: Annotated[Path | None, typer.Option(help="Write the ledger as JSON Lines here.")] = None,
) -> None:
    """Run a case. One line per request. Exit 1 if any expect block does not match."""
    result = _run(case)
    start = result.loaded.start_ms
    for trace in result.traces:
        models = ",".join(a.model for a in trace.attempts) or "-"
        typer.echo(
            f"{trace.request_id} t={trace.arrived_ms - start} {trace.status.value} cache={trace.cache.value} "
            f"attempts={len(trace.attempts)} [{models}] cost_micro={trace.cost_micro} "
            f"flags={','.join(trace.flags) or '-'}"
        )
    if traces is not None:
        traces.write_text(to_jsonl(result.traces), encoding="utf-8")
    if ledger is not None:
        ledger.write_text(result.ledger.to_jsonl(), encoding="utf-8")
    for difference in result.differences:
        typer.echo(f"reconcile: {difference}")
    for _, message in result.mismatches:
        typer.echo(f"MISMATCH {message}")
    if result.mismatches:
        raise typer.Exit(1)
    typer.echo(f"{result.loaded.case.id}: all expectations matched ({', '.join(result.loaded.case.covers)})")


@app.command()
def explain(
    case: CasePath, request_id: Annotated[str, typer.Argument(help="The request to explain.")]
) -> None:
    """Print one request's route reasons, events and attempts."""
    result = _run(case)
    found = [t for t in result.traces if t.request_id == request_id]
    if not found:
        typer.echo(f"error: no request {request_id!r} in {case}", err=True)
        raise typer.Exit(2)
    start = result.loaded.start_ms
    for trace in found:
        typer.echo(
            f"request {trace.request_id} ({trace.team}/{trace.app}/{trace.use_case}/{trace.task_class})"
        )
        typer.echo(
            f"  arrived {format_utc(trace.arrived_ms)}  status {trace.status.value}"
            f"  cache {trace.cache.value}"
        )
        if trace.route is not None:
            typer.echo(f"  route: rule {trace.route.rule_id}, chain [{', '.join(trace.route.chain)}]")
            for reason in trace.route.reasons:
                detail = ", ".join(f"{k}={v}" for k, v in sorted(reason.detail.items()))
                typer.echo(f"    {reason.code} {reason.subject}" + (f" ({detail})" if detail else ""))
        for event in trace.events:
            detail = ", ".join(f"{k}={v}" for k, v in sorted(event.detail.items()))
            typer.echo(
                f"  event t={event.at_ms - start} {event.code} {event.subject}"
                + (f" ({detail})" if detail else "")
            )
        for a in trace.attempts:
            typer.echo(
                f"  attempt {a.attempt} {a.model} t={a.started_ms - start}..{a.finished_ms - start} "
                f"{a.outcome.value} reserved_micro={a.reserved_micro} cost_micro={a.cost_micro} "
                f"(seq {a.reserve_seq}/{a.settle_seq})"
            )
        typer.echo(
            f"  cost {format_usd(trace.cost_micro)} USD (illustrative)  flags {','.join(trace.flags) or '-'}"
        )


@app.command()
def report(
    case: CasePath,
    output: Annotated[Path | None, typer.Option("-o", "--output", help="Write the report here.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="JSON instead of Markdown.")] = False,
) -> None:
    """Print the daily per-team cost report. Exit 1 if reconcile finds a difference."""
    result = _run(case)
    if result.differences or result.report_rows is None:
        for difference in result.differences:
            typer.echo(f"reconcile: {difference}", err=True)
        raise typer.Exit(1)
    rows = result.report_rows
    text = (
        render_json(rows) if as_json else render_markdown(rows, f"Daily cost report: {result.loaded.case.id}")
    )
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")
