"""`tracefork eval`: run an evaluation suite (TF-150..153, TF-181..182)."""

from pathlib import Path

import pydantic
import typer
import yaml
from tracefork.cli.output.console import console, error_console
from tracefork.cli.suites import format_json, format_junit, format_text, run_suite

_FORMATS = {"text": format_text, "json": format_json, "junit": format_junit}


def eval_suite(
    suite_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    output_format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text, json or junit."
    ),
) -> None:
    """Run a regression suite. Exit codes: 0 pass, 1 regression, 2 error, 3 invalid."""
    if output_format not in _FORMATS:
        error_console.print(
            f"[red]invalid input[/red]: --format accepts {sorted(_FORMATS)}, got {output_format!r}"
        )
        raise typer.Exit(code=3)
    try:
        result = run_suite(suite_path)
    except (yaml.YAMLError, pydantic.ValidationError, ValueError) as exc:
        error_console.print(f"[red]invalid suite[/red]: {exc}")
        raise typer.Exit(code=3) from exc

    console.print(_FORMATS[output_format](result))
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)
