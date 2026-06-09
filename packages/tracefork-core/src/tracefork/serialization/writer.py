"""Fixture envelope construction (TF-011)."""

from datetime import UTC, datetime

from tracefork.models import Trace
from tracefork.serialization.fixture import (
    SUPPORTED_FIXTURE_VERSION,
    FixtureEnvelope,
    FixtureIntegrity,
)


def build_envelope(trace: Trace, *, created_at: datetime | None = None) -> FixtureEnvelope:
    """Build a fixture envelope for *trace*, computing its integrity digest."""
    envelope = FixtureEnvelope(
        fixture_version=SUPPORTED_FIXTURE_VERSION,
        created_at=created_at if created_at is not None else datetime.now(UTC),
        trace=trace,
        integrity=FixtureIntegrity(algorithm="sha256", digest=""),
    )
    integrity = FixtureIntegrity(algorithm="sha256", digest=envelope.payload_digest())
    return envelope.model_copy(update={"integrity": integrity})
