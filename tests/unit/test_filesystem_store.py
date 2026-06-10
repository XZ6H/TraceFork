"""TF-012: filesystem fixture storage."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tracefork.errors import FixtureCorruptError, FixtureNotFoundError
from tracefork.models import Provenance, Trace
from tracefork.serialization import FixtureEnvelope, build_envelope
from tracefork.storage import FilesystemFixtureStore, FixtureStore

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_envelope() -> FixtureEnvelope:
    trace = Trace(trace_id="tr_1", name="case", started_at=T0, provenance=Provenance())
    return build_envelope(trace, created_at=T0)


def test_store_satisfies_protocol(tmp_path: Path) -> None:
    assert isinstance(FilesystemFixtureStore(tmp_path), FixtureStore)


def test_save_load_round_trip(tmp_path: Path) -> None:
    store = FilesystemFixtureStore(tmp_path)
    envelope = make_envelope()
    store.save("refund-case", envelope)
    assert store.load("refund-case") == envelope


def test_save_creates_missing_root(tmp_path: Path) -> None:
    store = FilesystemFixtureStore(tmp_path / "nested" / "fixtures")
    store.save("case", make_envelope())
    assert (tmp_path / "nested" / "fixtures" / "case.json").exists()


def test_list_returns_sorted_names(tmp_path: Path) -> None:
    store = FilesystemFixtureStore(tmp_path)
    for name in ("b-case", "a-case", "c-case"):
        store.save(name, make_envelope())
    assert store.list() == ["a-case", "b-case", "c-case"]


def test_list_empty_store(tmp_path: Path) -> None:
    assert FilesystemFixtureStore(tmp_path).list() == []


def test_list_ignores_foreign_files(tmp_path: Path) -> None:
    store = FilesystemFixtureStore(tmp_path)
    store.save("case", make_envelope())
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    assert store.list() == ["case"]


def test_load_missing_fixture_raises(tmp_path: Path) -> None:
    with pytest.raises(FixtureNotFoundError):
        FilesystemFixtureStore(tmp_path).load("nope")


def test_rejects_path_traversal(tmp_path: Path) -> None:
    store = FilesystemFixtureStore(tmp_path)
    envelope = make_envelope()
    with pytest.raises(ValueError, match="fixture name"):
        store.save("../evil", envelope)
    with pytest.raises(ValueError, match="fixture name"):
        store.load("../evil")
    with pytest.raises(ValueError, match="fixture name"):
        store.load(r"sub\\dir\\case")


def test_rejects_empty_and_hidden_names(tmp_path: Path) -> None:
    store = FilesystemFixtureStore(tmp_path)
    for name in ("", ".", "..", ".hidden"):
        with pytest.raises(ValueError, match="fixture name"):
            store.load(name)


def test_load_detects_tampered_file(tmp_path: Path) -> None:
    store = FilesystemFixtureStore(tmp_path)
    store.save("case", make_envelope())
    data = json.loads((tmp_path / "case.json").read_text(encoding="utf-8"))
    data["integrity"]["digest"] = "0" * 64
    (tmp_path / "case.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FixtureCorruptError):
        store.load("case")
