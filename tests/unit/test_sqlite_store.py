"""Item 6d: SQLite fixture store — same protocol, persistent backend."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tracefork.errors import FixtureCorruptError, FixtureNotFoundError
from tracefork.models import Provenance, Trace
from tracefork.serialization import FixtureEnvelope, build_envelope
from tracefork.storage import FixtureStore, SQLiteFixtureStore

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_envelope(name_hint: str = "case") -> FixtureEnvelope:
    trace = Trace(
        trace_id=f"tr_{name_hint}", name=name_hint, started_at=T0, provenance=Provenance()
    )
    return build_envelope(trace, created_at=T0)


def test_store_satisfies_protocol(tmp_path: Path) -> None:
    with SQLiteFixtureStore(tmp_path / "fixtures.db") as store:
        assert isinstance(store, FixtureStore)


def test_save_load_round_trip(tmp_path: Path) -> None:
    with SQLiteFixtureStore(tmp_path / "fixtures.db") as store:
        envelope = make_envelope()
        store.save("refund-case", envelope)
        assert store.load("refund-case") == envelope


def test_persists_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "fixtures.db"
    with SQLiteFixtureStore(db) as store:
        store.save("case", make_envelope())
    with SQLiteFixtureStore(db) as reopened:
        assert reopened.load("case").trace.name == "case"


def test_save_overwrites_existing(tmp_path: Path) -> None:
    with SQLiteFixtureStore(tmp_path / "fixtures.db") as store:
        store.save("case", make_envelope())
        updated = build_envelope(
            Trace(trace_id="tr_2", name="updated", started_at=T0, provenance=Provenance()),
            created_at=T0,
        )
        store.save("case", updated)
        assert store.load("case").trace.name == "updated"


def test_list_sorted(tmp_path: Path) -> None:
    with SQLiteFixtureStore(tmp_path / "fixtures.db") as store:
        for name in ("b", "a", "c"):
            store.save(name, make_envelope(name))
        assert store.list() == ["a", "b", "c"]


def test_list_empty(tmp_path: Path) -> None:
    with SQLiteFixtureStore(tmp_path / "fixtures.db") as store:
        assert store.list() == []


def test_load_missing_raises(tmp_path: Path) -> None:
    with SQLiteFixtureStore(tmp_path / "fixtures.db") as store, pytest.raises(FixtureNotFoundError):
        store.load("nope")


def test_rejects_path_traversal_names(tmp_path: Path) -> None:
    with SQLiteFixtureStore(tmp_path / "fixtures.db") as store:
        with pytest.raises(ValueError, match="fixture name"):
            store.save("../evil", make_envelope())
        with pytest.raises(ValueError, match="fixture name"):
            store.load("../evil")


def test_load_tampered_content_raises_corrupt(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "fixtures.db"
    with SQLiteFixtureStore(db) as store:
        store.save("case", make_envelope())
    connection = sqlite3.connect(db)
    try:
        connection.execute("UPDATE fixtures SET payload = ? WHERE name = 'case'", (b"tampered",))
        connection.commit()
    finally:
        connection.close()
    with SQLiteFixtureStore(db) as store, pytest.raises(FixtureCorruptError):
        store.load("case")
