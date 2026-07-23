"""SQLite fixture store (item 6d).

Same :class:`FixtureStore` protocol as the filesystem backend, backed by a
single SQLite database file. Payloads are stored as canonical JSON bytes and
re-validated through the envelope reader on load, so tampering is caught the
same way.
"""

import sqlite3
from pathlib import Path
from types import TracebackType

from tracefork.errors import FixtureCorruptError, FixtureNotFoundError
from tracefork.serialization import FixtureEnvelope, parse_envelope
from tracefork.storage.base import validate_fixture_name

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (
    name TEXT PRIMARY KEY,
    payload BLOB NOT NULL
);
"""


class SQLiteFixtureStore:
    """Store fixture envelopes in a SQLite database file."""

    def __init__(self, path: Path | str) -> None:
        self._connection = sqlite3.connect(str(path))
        self._connection.executescript(_SCHEMA)

    def save(self, name: str, envelope: FixtureEnvelope) -> None:
        validate_fixture_name(name)
        self._connection.execute(
            "INSERT OR REPLACE INTO fixtures (name, payload) VALUES (?, ?)",
            (name, envelope.to_json_bytes()),
        )
        self._connection.commit()

    def load(self, name: str) -> FixtureEnvelope:
        validate_fixture_name(name)
        row = self._connection.execute(
            "SELECT payload FROM fixtures WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            msg = f"fixture {name!r} not found in database"
            raise FixtureNotFoundError(msg)
        try:
            return parse_envelope(bytes(row[0]))
        except FixtureCorruptError as exc:
            msg = f"fixture {name!r} is corrupt: {exc}"
            raise FixtureCorruptError(msg) from exc

    def list(self) -> list[str]:
        return sorted(name for (name,) in self._connection.execute("SELECT name FROM fixtures"))

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SQLiteFixtureStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
