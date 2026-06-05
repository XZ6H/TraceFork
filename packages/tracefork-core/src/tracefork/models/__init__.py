"""Trace domain models."""

from tracefork.models.provenance import Provenance, capture_git_provenance
from tracefork.models.span import Span, SpanError, SpanKind, SpanStatus
from tracefork.models.trace import SCHEMA_VERSION, Trace, TraceStatus

__all__ = [
    "SCHEMA_VERSION",
    "Provenance",
    "Span",
    "SpanError",
    "SpanKind",
    "SpanStatus",
    "Trace",
    "TraceStatus",
    "capture_git_provenance",
]
