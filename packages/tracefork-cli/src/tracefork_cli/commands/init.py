"""`tracefork init`: create the local project layout (TF-100)."""

from pathlib import Path

from tracefork_cli.output.console import console

_CONFIG_TEMPLATE = """\
# TraceFork configuration
# Replay policy defaults and canonicalization rules are configured here.
# See https://github.com/tracefork/tracefork for the schema.

canonicalization:
  ignore: []

regression:
  tokens:
    max_increase_percent: 20
  cost:
    max_increase_percent: 15
"""


def init() -> None:
    """Create the .tracefork project layout."""
    root = Path(".tracefork")
    (root / "fixtures").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    config = root / "config.yaml"
    if not config.exists():
        config.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
    console.print("[green]Initialized[/green] .tracefork/ (fixtures/, runs/, config.yaml)")
