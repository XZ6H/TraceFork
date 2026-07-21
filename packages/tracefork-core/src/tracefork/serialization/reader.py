"""Fixture reading with version and integrity validation (TF-011).

Loading fails closed: unsupported versions raise ``FixtureVersionError``,
malformed payloads or digest mismatches raise ``FixtureCorruptError``. Nothing
is ever best-effort parsed.
"""

import json
from typing import Any

import pydantic

from tracefork.errors import FixtureCorruptError, FixtureVersionError, GraphError
from tracefork.serialization.fixture import (
    SUPPORTED_FIXTURE_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    FixtureEnvelope,
)
from tracefork.trajectory import build_graph


def parse_envelope(data: bytes) -> FixtureEnvelope:
    """Parse and fully validate fixture *data* (canonical JSON bytes)."""
    try:
        raw: Any = json.loads(data)
    except json.JSONDecodeError as exc:
        msg = f"fixture is not valid JSON: {exc}"
        raise FixtureCorruptError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"fixture must be a JSON object, got {type(raw).__name__}"
        raise FixtureCorruptError(msg)

    version = raw.get("fixture_version")
    if version != SUPPORTED_FIXTURE_VERSION:
        msg = f"unsupported fixture_version {version!r}, supported: {SUPPORTED_FIXTURE_VERSION!r}"
        raise FixtureVersionError(msg)

    trace_raw = raw.get("trace")
    if isinstance(trace_raw, dict):
        schema_version = trace_raw.get("schema_version")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            msg = (
                f"unsupported trace schema_version {schema_version!r}, "
                f"supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
            raise FixtureVersionError(msg)

    try:
        envelope = FixtureEnvelope.model_validate(raw)
    except pydantic.ValidationError as exc:
        msg = f"fixture failed schema validation: {exc.error_count()} error(s)"
        raise FixtureCorruptError(msg) from exc

    expected = envelope.payload_digest()
    if envelope.integrity.digest != expected:
        msg = (
            "fixture integrity check failed: digest mismatch "
            f"(declared {envelope.integrity.digest[:16]}..., computed {expected[:16]}...)"
        )
        raise FixtureCorruptError(msg)

    # ADR 0005: loading validates the span graph, so corrupted or malformed
    # imported traces fail here instead of surfacing mid-replay.
    try:
        build_graph(envelope.trace)
    except GraphError as exc:
        msg = f"trace graph is invalid: {exc}"
        raise FixtureCorruptError(msg) from exc
    return envelope
