"""`tracefork replay`: replay a fixture under a policy (TF-103, TF-094)."""

import asyncio
import importlib
from pathlib import Path
from typing import Any

import typer
from tracefork.bootstrap import registry as bootstrap_registry
from tracefork.boundaries import ReplayMode, ReplayPolicy
from tracefork.errors import FixtureError, ReplayError
from tracefork.replay import ReplaySession
from tracefork.serialization import FixtureEnvelope, parse_envelope
from tracefork_cli.output.console import console, error_console

_LIVE_TARGETS = {"llm": "llm", "http": "http", "tools": "tool"}


def _load_entrypoint(spec: str) -> Any:
    if ":" not in spec:
        msg = f"entrypoint must be module:function, got {spec!r}"
        raise ValueError(msg)
    module_name, function_name = spec.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        entry = getattr(module, function_name)
    except (ImportError, AttributeError) as exc:
        msg = f"cannot load entrypoint {spec!r}: {exc}"
        raise ValueError(msg) from exc
    if not callable(entry):
        msg = f"entrypoint {spec!r} is not callable"
        raise ValueError(msg)
    return entry


def _build_policy(live: list[str], live_tool: list[str]) -> ReplayPolicy:
    families: dict[str, ReplayMode] = {}
    for target in live:
        family = _LIVE_TARGETS.get(target)
        if family is None:
            msg = f"--live accepts {sorted(_LIVE_TARGETS)}, got {target!r}"
            raise ValueError(msg)
        families[family] = ReplayMode.LIVE
    tools = dict.fromkeys(live_tool, ReplayMode.LIVE)
    return ReplayPolicy(families=families, tools=tools, default=ReplayMode.REPLAY)


def _counts_by_family(matched: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {"llm": 0, "tool": 0, "http": 0}
    for invocation in matched:
        family = invocation.boundary_type.split(".", 1)[0]
        counts[family] = counts.get(family, 0) + 1
    return counts


def _print_report(envelope: FixtureEnvelope, session: ReplaySession, policy: ReplayPolicy) -> None:
    result = session.result
    counts = _counts_by_family(session.matched)
    console.print(f"Replay: [bold]{envelope.trace.name}[/bold]")
    console.print("  LLM calls replayed:   " + str(counts.get("llm", 0)))
    console.print("  Tool calls replayed:  " + str(counts.get("tool", 0)))
    console.print("  HTTP calls replayed:  " + str(counts.get("http", 0)))
    console.print("  Live calls:           " + str(len(result.live_boundaries)))
    for boundary in result.live_boundaries:
        console.print(f"    [yellow]LIVE[/yellow] {boundary}")
    console.print("  Unexpected calls:     " + str(len(result.unexpected)))
    console.print("  Unused recordings:    " + str(len(result.unused_recordings)))
    console.print(f"  Policy:               {policy.model_dump_json()}")
    verdict = "[green]PASS[/green]" if result.status.value == "passed" else "[red]FAIL[/red]"
    console.print(f"\n{verdict}")


def replay(
    fixture_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    entrypoint: str = typer.Option(
        ...,
        "--entrypoint",
        "-e",
        help="module:function, an async callable receiving the trace input.",
    ),
    live: list[str] = typer.Option(
        [], "--live", help="Run a boundary family live (llm|http|tools)."
    ),
    live_tool: list[str] = typer.Option([], "--live-tool", help="Run one tool live by name."),
) -> None:
    """Replay a fixture while running the current agent code behind --entrypoint."""
    try:
        envelope = parse_envelope(fixture_path.read_bytes())
        policy = _build_policy(list(live), list(live_tool))
        entry = _load_entrypoint(entrypoint)
    except FixtureError as exc:
        error_console.print(f"[red]invalid fixture[/red]: {exc}")
        raise typer.Exit(code=3) from exc
    except ValueError as exc:
        error_console.print(f"[red]invalid input[/red]: {exc}")
        raise typer.Exit(code=3) from exc

    # Entrypoint modules register handlers on the bootstrap registry at import
    # time, so the session must use that same registry for restore.
    registry = bootstrap_registry()
    session = ReplaySession(fixture=envelope, policy=policy, registry=registry)

    async def run() -> tuple[int, str]:
        mismatched = False
        output: Any = None
        with session:
            try:
                output = await entry(envelope.trace.input)
            except ReplayError as exc:
                error_console.print(f"[red]replay mismatch[/red]\n{exc}")
                mismatched = True
            except Exception as exc:
                error_console.print(f"[red]execution error[/red]: {type(exc).__name__}: {exc}")
                return 2, ""
        _print_report(envelope, session, policy)
        if mismatched or session.result.status.value != "passed":
            return 1, ""
        console.print(f"output: {output!r}" if output is not None else "")
        return 0, ""

    exit_code, _ = asyncio.run(run())
    if exit_code:
        raise typer.Exit(code=exit_code)
