"""Fixture store protocol (TF-012).

Backends persist fixture envelopes by name. The filesystem backend ships with
the core; SQLite arrives later if needed.
"""

from typing import Protocol, runtime_checkable

from tracefork.serialization import FixtureEnvelope


@runtime_checkable
class FixtureStore(Protocol):
    """Storage contract for fixture envelopes."""

    def save(self, name: str, envelope: FixtureEnvelope) -> None:
        """Persist *envelope* under *name*."""
        ...

    def load(self, name: str) -> FixtureEnvelope:
        """Load the envelope stored under *name*."""
        ...

    def list(self) -> list[str]:
        """List stored fixture names, sorted."""
        ...
