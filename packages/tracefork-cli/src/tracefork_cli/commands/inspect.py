"""`tracefork inspect`: summarize a fixture (TF-102)."""

from collections import Counter
from pathlib import Path

import typer
from tracefork.errors import FixtureError
from tracefork.serialization import parse_envelope
from tracefork_cli.output.console import console, error_console


def inspect(fixture_path: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    """Print a human-readable summary of a fixture."""
    try:
        envelope = parse_envelope(fixture_path.read_bytes())
    except FixtureError as exc:
        error_console.print(f"[red]invalid fixture[/red]: {exc}")
        raise typer.Exit(code=3) from exc

    trace = envelope.trace
    kinds = Counter(span.kind.value for span in trace.spans)
    tokens = sum(
        (invocation.metadata.get("usage") or {}).get("total_tokens") or 0
        for invocation in trace.invocations
    )
    duration = None
    if trace.completed_at is not None:
        duration = (trace.completed_at - trace.started_at).total_seconds()

    console.print(f"Trace [bold]{trace.name}[/bold]")
    console.print(f"  status:   {trace.status.value}")
    console.print(f"  spans:    {len(trace.spans)}")
    for kind in sorted(kinds):
        console.print(f"    {kind}: {kinds[kind]}")
    console.print(f"  boundaries: {len(trace.invocations)}")
    if tokens:
        console.print(f"  tokens:   {tokens}")
    if duration is not None:
        console.print(f"  duration: {duration:.2f}s")
