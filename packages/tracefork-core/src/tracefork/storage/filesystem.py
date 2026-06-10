"""Filesystem fixture store (TF-012).

Layout: ``<root>/<name>.json``. Writes are atomic (temp file + replace) so a
crash never leaves a half-written fixture behind. Fixture names are validated
to prevent path traversal.
"""

import os
import re
import tempfile
from pathlib import Path

from tracefork.errors import FixtureCorruptError, FixtureNotFoundError
from tracefork.serialization import FixtureEnvelope, parse_envelope

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SUFFIX = ".json"


class FilesystemFixtureStore:
    """Store fixture envelopes as JSON files under a root directory."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def save(self, name: str, envelope: FixtureEnvelope) -> None:
        _validate_name(name)
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / f"{name}{_SUFFIX}"
        data = envelope.to_json_bytes()
        handle, tmp_name = tempfile.mkstemp(dir=self._root, suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def load(self, name: str) -> FixtureEnvelope:
        _validate_name(name)
        target = self._root / f"{name}{_SUFFIX}"
        try:
            data = target.read_bytes()
        except FileNotFoundError as exc:
            msg = f"fixture {name!r} not found in {self._root}"
            raise FixtureNotFoundError(msg) from exc
        try:
            return parse_envelope(data)
        except FixtureCorruptError as exc:
            msg = f"fixture {name!r} is corrupt: {exc}"
            raise FixtureCorruptError(msg) from exc

    def list(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(
            path.name[: -len(_SUFFIX)] for path in self._root.glob(f"*{_SUFFIX}") if path.is_file()
        )


def _validate_name(name: str) -> None:
    if not _NAME_PATTERN.fullmatch(name):
        msg = f"invalid fixture name {name!r}: must match {_NAME_PATTERN.pattern}"
        raise ValueError(msg)
