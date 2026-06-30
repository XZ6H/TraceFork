"""Rich console helpers for the CLI."""

from rich.console import Console

console = Console(stderr=False)
error_console = Console(stderr=True)
