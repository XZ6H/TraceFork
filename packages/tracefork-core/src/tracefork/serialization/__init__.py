"""Serialization: versioned fixture envelopes, reading and writing (TF-011)."""

from tracefork.serialization.fixture import (
    SUPPORTED_FIXTURE_VERSION,
    FixtureEnvelope,
    FixtureIntegrity,
)
from tracefork.serialization.reader import parse_envelope
from tracefork.serialization.writer import build_envelope

__all__ = [
    "SUPPORTED_FIXTURE_VERSION",
    "FixtureEnvelope",
    "FixtureIntegrity",
    "build_envelope",
    "parse_envelope",
]
