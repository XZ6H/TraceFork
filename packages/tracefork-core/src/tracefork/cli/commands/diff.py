"""`tracefork diff`: report trajectory and resource differences (TF-135)."""

from pathlib import Path

import typer
from tracefork.cli.output.console import console, error_console
from tracefork.diff import diff_traces
from tracefork.errors import FixtureError
from tracefork.serialization import parse_envelope


def diff(
    baseline: Path = typer.Argument(..., exists=True, dir_okay=False),
    candidate: Path = typer.Argument(..., exists=True, dir_okay=False),
) -> None:
    """Print the regression report for two fixtures."""
    try:
        baseline_envelope = parse_envelope(baseline.read_bytes())
        candidate_envelope = parse_envelope(candidate.read_bytes())
    except FixtureError as exc:
        error_console.print(f"[red]invalid fixture[/red]: {exc}")
        raise typer.Exit(code=3) from exc

    result = diff_traces(baseline_envelope.trace, candidate_envelope.trace)

    console.print("[bold]TRACE REGRESSION REPORT[/bold]")
    console.print(f"baseline:  {baseline_envelope.trace.name}")
    console.print(f"candidate: {candidate_envelope.trace.name}")

    console.print("\n[bold]Trajectory[/bold]")
    for op in result.ops:
        if op.op == "match" and op.baseline is not None:
            console.print(f"  {op.baseline.kind.value}:{op.baseline.name}")
        elif op.op == "insert" and op.candidate is not None:
            console.print(f"  [green]+ {op.candidate.kind.value}:{op.candidate.name}[/green]")
        elif op.op == "remove" and op.baseline is not None:
            console.print(f"  [red]- {op.baseline.kind.value}:{op.baseline.name}[/red]")

    if result.first_divergence is not None:
        divergence = result.first_divergence
        console.print(
            f"\n[bold]First divergence[/bold]: {divergence.reason} "
            f"at alignment step {divergence.position}"
        )

    console.print("\n[bold]Resources[/bold]")
    for delta in result.resources:
        if delta.baseline is None and delta.candidate is None:
            continue
        change = "n/a" if delta.change_percent is None else f"{delta.change_percent:+.1f}%"
        console.print(f"  {delta.name}: {delta.baseline} -> {delta.candidate} ({change})")
