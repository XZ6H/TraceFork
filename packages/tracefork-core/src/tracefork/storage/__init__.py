"""Storage backends for fixture envelopes."""

from tracefork.storage.base import FixtureStore
from tracefork.storage.filesystem import FilesystemFixtureStore

__all__ = ["FilesystemFixtureStore", "FixtureStore"]
