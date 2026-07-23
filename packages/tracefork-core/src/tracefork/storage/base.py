"""Fixture store protocol (TF-012).

Backends persist fixture envelopes by name. The filesystem and SQLite
backends ship with the core; both share the same name-validation rules.
"""

import re
from typing import Protocol, runtime_checkable

from tracefork.serialization import FixtureEnvelope

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_fixture_name(name: str) -> None:
    """Validate a fixture name (shared by all backends; blocks traversal)."""
    if not _NAME_PATTERN.fullmatch(name):
        msg = f"invalid fixture name {name!r}: must match {_NAME_PATTERN.pattern}"
        raise ValueError(msg)


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
