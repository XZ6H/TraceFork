"""`tracefork record`: run a Python script with recording enabled (TF-101)."""

import os
import subprocess
import sys
from pathlib import Path

import typer
from tracefork.cli.output.console import error_console


def record(
    ctx: typer.Context,
    name: str = typer.Option(None, "--name", "-n", help="Fixture name (default: script stem)."),
) -> None:
    """Run a script under recording: tracefork record python script.py [args]"""
    args = list(ctx.args)
    if args and args[0] == "python":
        args = args[1:]
    if not args:
        error_console.print("usage: tracefork record [--name NAME] python SCRIPT [ARGS...]")
        raise typer.Exit(code=3)
    script = Path(args[0])
    if not script.exists():
        error_console.print(f"script not found: {script}")
        raise typer.Exit(code=3)

    record_name = name if name else script.stem
    env = {**os.environ, "TRACEFORK_RECORD_NAME": record_name}
    completed = subprocess.run(
        [sys.executable, "-m", "tracefork.cli.bootstrap", str(script), *args[1:]],
        env=env,
        check=False,
    )
    raise typer.Exit(code=completed.returncode)
