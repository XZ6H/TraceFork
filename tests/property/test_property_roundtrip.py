"""Property-based tests (plan §34): serialization round trips via Hypothesis.

deserialize(serialize(x)) == x for arbitrary valid traces and envelopes.
"""

from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tracefork.models import BoundaryInvocation, Provenance, Span, SpanKind, SpanStatus, Trace
from tracefork.serialization import build_envelope, parse_envelope

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)

json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-(10**6), 10**6) | st.text(max_size=16),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=4)
    ),
    max_leaves=12,
)

kinds = st.sampled_from(list(SpanKind))
statuses = st.sampled_from([SpanStatus.OK, SpanStatus.ERROR])


def _spans_from(items: list[tuple]) -> list[Span]:
    """Build an acyclic span tree: each span may parent to an earlier one."""
    spans: list[Span] = []
    for index, (kind, name, status, payload) in enumerate(items):
        parent = spans[index - 1].span_id if spans and index % 3 == 0 else None
        spans.append(
            Span(
                span_id=f"sp_{index}",
                parent_span_id=parent,
                kind=kind,
                name=name,
                started_at=T0 + timedelta(microseconds=index),
                completed_at=T0 + timedelta(microseconds=index + 1),
                input=payload,
                output=payload,
                status=status,
            )
        )
    return spans


span_trees = st.lists(
    st.tuples(kinds, st.text(min_size=1, max_size=12), statuses, json_values),
    max_size=8,
).map(_spans_from)

invocation_lists = st.lists(
    st.tuples(st.sampled_from(["tool.python", "llm.openai", "http.httpx"]), json_values),
    max_size=6,
).map(
    lambda items: [
        BoundaryInvocation(
            boundary_type=boundary_type,
            name=f"call_{position}",
            request=payload,
            response=payload,
            span_id="sp_0",
            occurrence=0,
        )
        for position, (boundary_type, payload) in enumerate(items)
    ]
)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(span_trees=span_trees, invocations=invocation_lists)
def test_trace_json_round_trip_is_lossless(
    span_trees: list[Span], invocations: list[BoundaryInvocation]
) -> None:
    trace = Trace(
        trace_id="tr_1",
        name="property-case",
        started_at=T0,
        provenance=Provenance(),
        spans=span_trees,
        invocations=invocations,
    )
    restored = Trace.model_validate_json(trace.model_dump_json())
    assert restored == trace


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(span_trees=span_trees, invocations=invocation_lists)
def test_envelope_round_trip_preserves_digest(
    span_trees: list[Span], invocations: list[BoundaryInvocation]
) -> None:
    trace = Trace(
        trace_id="tr_1",
        name="property-case",
        started_at=T0,
        provenance=Provenance(),
        spans=span_trees,
        invocations=invocations,
    )
    envelope = build_envelope(trace)
    restored = parse_envelope(envelope.to_json_bytes())
    assert restored == envelope
    assert restored.integrity.digest == envelope.integrity.digest


@settings(max_examples=30, deadline=None)
@given(payload=json_values)
def test_canonical_serialization_round_trips_through_json(payload) -> None:
    import json

    from tracefork.canonicalization import canonical_json

    reparsed = json.loads(canonical_json(payload))
    assert canonical_json(reparsed) == canonical_json(payload)
