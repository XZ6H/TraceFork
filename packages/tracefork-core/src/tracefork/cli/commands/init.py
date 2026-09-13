"""`tracefork init`: create the local project layout (TF-100)."""

from pathlib import Path

from tracefork.cli.output.console import console

_CONFIG_TEMPLATE = """\
# TraceFork configuration (reserved for future use).
#
# Nothing reads this file yet. Suite-level replay policies, expectations and
# thresholds are configured per case in suite YAML files (see `tracefork eval
# --help` and docs/cli.md). Global defaults will be wired to this file in a
# future release; keys below are reserved and currently have no effect.
#
# canonicalization:
#   ignore: []
#
# regression:
#   tokens:
#     max_increase_percent: 20
#   cost:
#     max_increase_percent: 15
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
