"""Versioned fixture envelope with integrity digest (TF-011, ADR 0002).

The digest covers the payload (fixture version, creation timestamp, trace) —
never the integrity block itself. It is computed over the canonical JSON
serialization so it is stable across runs and platforms.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tracefork.canonicalization import canonical_json_bytes
from tracefork.models import SCHEMA_VERSION, Trace

SUPPORTED_FIXTURE_VERSION = "1"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})


class FixtureIntegrity(BaseModel):
    """Integrity block of a fixture envelope."""

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["sha256"]
    digest: str


class FixtureEnvelope(BaseModel):
    """Immutable envelope wrapping a trace for use as a regression fixture."""

    model_config = ConfigDict(extra="forbid")

    fixture_version: str = SUPPORTED_FIXTURE_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace: Trace
    integrity: FixtureIntegrity

    def to_json_bytes(self) -> bytes:
        """Serialize the complete envelope to canonical JSON bytes."""
        return canonical_json_bytes(self.model_dump(mode="json"))

    def payload_digest(self) -> str:
        """Compute the SHA-256 digest over the envelope payload."""
        payload: dict[str, Any] = {
            "fixture_version": self.fixture_version,
            "created_at": self.created_at,
            "trace": self.trace.model_dump(mode="json"),
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
