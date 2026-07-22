"""Fixture loading must validate the trace schema version and graph structure.

ADR 0002: unknown trace schema versions are rejected, never best-effort parsed.
ADR 0005: loading validates the span graph — cycles, duplicates and missing
parents are `FixtureCorruptError`s with precise messages. The realistic source
of invalid graphs is a buggy exporter sealing an invalid trace, so the tests
build envelopes around invalid traces directly (digests honestly match).
"""

from datetime import UTC, datetime

import pytest
from tracefork.errors import FixtureCorruptError, FixtureVersionError
from tracefork.models import Provenance, Span, SpanKind, Trace
from tracefork.serialization import build_envelope, parse_envelope

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_trace(spans: list[Span] | None = None, schema_version: str = "1.0") -> Trace:
    return Trace(
        trace_id="tr_1",
        name="case",
        started_at=T0,
        provenance=Provenance(),
        schema_version=schema_version,
        spans=spans or [],
    )


def span(
    span_id: str, name: str, kind: SpanKind = SpanKind.TOOL, parent: str | None = None
) -> Span:
    return Span(span_id=span_id, parent_span_id=parent, kind=kind, name=name, started_at=T0)


def test_future_trace_schema_version_rejected() -> None:
    envelope = build_envelope(make_trace(schema_version="2.0"))
    with pytest.raises(FixtureVersionError, match="schema_version"):
        parse_envelope(envelope.to_json_bytes())


def test_current_schema_version_loads() -> None:
    envelope = build_envelope(make_trace())
    assert parse_envelope(envelope.to_json_bytes()).trace.schema_version == "1.0"


def test_duplicate_span_ids_rejected_at_load() -> None:
    envelope = build_envelope(make_trace([span("sp_1", "a"), span("sp_1", "b", SpanKind.LLM)]))
    with pytest.raises(FixtureCorruptError, match="duplicate"):
        parse_envelope(envelope.to_json_bytes())


def test_missing_parent_rejected_at_load() -> None:
    envelope = build_envelope(make_trace([span("sp_1", "a", parent="sp_ghost")]))
    with pytest.raises(FixtureCorruptError, match="missing parent"):
        parse_envelope(envelope.to_json_bytes())


def test_cycle_rejected_at_load() -> None:
    spans = [
        span("sp_1", "a", SpanKind.AGENT, parent="sp_2"),
        span("sp_2", "b", SpanKind.LLM, parent="sp_1"),
    ]
    envelope = build_envelope(make_trace(spans))
    with pytest.raises(FixtureCorruptError, match="cycle"):
        parse_envelope(envelope.to_json_bytes())


def test_valid_tree_loads_cleanly() -> None:
    envelope = build_envelope(
        make_trace(
            [
                span("sp_1", "agent", SpanKind.AGENT),
                span("sp_2", "llm", SpanKind.LLM, parent="sp_1"),
                span("sp_3", "tool", parent="sp_1"),
            ]
        )
    )
    restored = parse_envelope(envelope.to_json_bytes())
    assert len(restored.trace.spans) == 3
