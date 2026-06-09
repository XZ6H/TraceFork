"""Canonical JSON serialization (TF-013).

The canonical form is the fingerprint and integrity basis for everything
TraceFork stores: UTF-8, sorted keys, compact stable separators, normalized
datetimes (UTC with ``Z`` suffix), no nondeterministic dictionary ordering.
"""

import json
import math
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

__all__ = ["canonical_json", "canonical_json_bytes", "canonicalize"]


def canonicalize(value: Any) -> Any:
    """Return a JSON-ready, deterministically ordered representation of *value*.

    Supported: ``None``, booleans, integers, finite floats, strings, mappings,
    sequences, enums, UUIDs, dates and datetimes. Anything else is rejected
    with ``TypeError`` — arbitrary objects are never silently stringified.
    """
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            msg = f"float value {value!r} is not canonicalizable"
            raise TypeError(msg)
        return value
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return canonicalize(value.value)
    if isinstance(value, dict):
        return {str(key): canonicalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [canonicalize(item) for item in value]
    msg = f"object of type {type(value).__name__} is not canonicalizable"
    raise TypeError(msg)


def canonical_json(value: Any) -> str:
    """Serialize *value* to canonical JSON text."""
    return json.dumps(
        canonicalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize *value* to canonical JSON bytes (UTF-8)."""
    return canonical_json(value).encode("utf-8")


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
