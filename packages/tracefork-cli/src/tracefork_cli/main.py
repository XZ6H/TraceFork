"""TraceFork command-line interface (TF-100..104).

Exit codes: 0 success, 1 regression/mismatch, 2 execution error,
3 invalid input or fixture.
"""

from typing import Any

import tracefork
import typer

from tracefork_cli.commands import diff, init, inspect, record, replay
from tracefork_cli.output.console import error_console

app = typer.Typer(
    name="tracefork",
    help="Replay AI agent failures locally and turn them into regression tests.",
    no_args_is_help=True,
)
app.command("init")(init.init)
app.command("record", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(
    record.record
)
app.command("inspect")(inspect.inspect)
app.command("replay")(replay.replay)
app.command("diff")(diff.diff)


def _version_callback(value: bool) -> None:
    if value:
        error_console.print(f"tracefork {tracefork.__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True
    ),
) -> None:
    """TraceFork: record, replay and diff AI agent executions."""


def main() -> Any:
    app()
