"""TraceFork core: record, replay and diff AI agent executions."""

from tracefork.recording import Recording, record, span

__version__ = "0.1.0"

__all__ = ["Recording", "__version__", "record", "span"]
