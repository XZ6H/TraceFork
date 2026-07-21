"""Property-based tests: canonicalization determinism, redaction, graph validity."""

from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tracefork.canonicalization import Canonicalizer, canonical_json
from tracefork.models import Provenance, Span, SpanKind, SpanStatus, Trace
from tracefork.redaction import DEFAULT_SECRET_KEYS, RedactionEngine
from tracefork.trajectory import build_graph

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)

json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-(10**6), 10**6) | st.text(max_size=16),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=4)
    ),
    max_leaves=12,
)


def _reverse_keys(value):
    if isinstance(value, dict):
        return {key: _reverse_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reverse_keys(item) for item in value]
    return value


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(payload=json_values)
def test_canonical_json_is_insertion_order_independent(payload) -> None:
    reordered = _reverse_keys(payload)
    assert canonical_json(payload) == canonical_json(reordered)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(payload=json_values, extra=st.text(min_size=0, max_size=8))
def test_fingerprints_survive_ignore_rule_changes_of_untouched_fields(payload, extra) -> None:
    """Adding ignore rules for keys that cannot occur must not change the fingerprint.

    The rule is longer than any generated payload key (max 8 chars), so it is
    guaranteed absent.
    """
    canonicalizer = Canonicalizer()
    with_rules = Canonicalizer(ignore=[f"__absent_{extra}__"])
    assert canonicalizer.fingerprint("tool", "t", payload) == with_rules.fingerprint(
        "tool", "t", payload
    )


secret_values = st.text(
    min_size=8,
    max_size=24,
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
)

secret_payloads = st.dictionaries(
    st.sampled_from(sorted(set(DEFAULT_SECRET_KEYS) | {"API_KEY", "Authorization", "PaSsWoRd"})),
    secret_values,
    min_size=1,
    max_size=4,
)


@settings(max_examples=30, deadline=None)
@given(payload=secret_payloads)
def test_redaction_ensures_secrets_never_survive(payload) -> None:
    engine = RedactionEngine()
    redacted = engine.apply(payload)
    text = canonical_json(redacted)
    for key, value in payload.items():
        assert value not in text, f"secret under {key} survived redaction"
    assert all(item == "[REDACTED]" for item in redacted.values())


def _spans_from(items: list[tuple]) -> list[Span]:
    """Build an acyclic span tree: each span may parent to an earlier one."""
    spans: list[Span] = []
    for index, (kind, name) in enumerate(items):
        parent = spans[index - 1].span_id if spans and index % 4 == 0 else None
        spans.append(
            Span(
                span_id=f"sp_{index}",
                parent_span_id=parent,
                kind=kind,
                name=name,
                started_at=T0 + timedelta(microseconds=index),
                status=SpanStatus.OK,
            )
        )
    return spans


span_trees = st.lists(
    st.tuples(st.sampled_from(list(SpanKind)), st.text(min_size=1, max_size=12)),
    max_size=10,
).map(_spans_from)


@settings(max_examples=30, deadline=None)
@given(spans=span_trees)
def test_valid_span_trees_always_build_graphs(spans: list[Span]) -> None:
    trace = Trace(trace_id="tr_1", name="case", started_at=T0, provenance=Provenance(), spans=spans)
    graph = build_graph(trace)
    assert len(graph.nodes) == len(spans)
    for span in spans:
        for child_id in graph.children_of(span.span_id):
            assert graph.nodes[child_id].parent_span_id == span.span_id
