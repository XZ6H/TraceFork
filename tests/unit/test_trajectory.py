"""TF-120..122: execution graph construction, validation, logical trajectory."""

from datetime import UTC, datetime

import pytest
from tracefork.errors import GraphError
from tracefork.models import Provenance, Span, SpanKind, Trace
from tracefork.trajectory import build_graph, logical_trajectory

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_span(span_id: str, name: str, kind: SpanKind, parent: str | None = None) -> Span:
    return Span(span_id=span_id, parent_span_id=parent, kind=kind, name=name, started_at=T0)


def make_trace(spans: list[Span]) -> Trace:
    return Trace(trace_id="tr_1", name="case", started_at=T0, provenance=Provenance(), spans=spans)


def test_graph_builds_parent_edges() -> None:
    trace = make_trace(
        [
            make_span("a", "agent", SpanKind.AGENT),
            make_span("b", "llm", SpanKind.LLM, parent="a"),
            make_span("c", "tool", SpanKind.TOOL, parent="a"),
            make_span("d", "http", SpanKind.HTTP, parent="c"),
        ]
    )
    graph = build_graph(trace)
    assert graph.children_of("a") == ["b", "c"]
    assert graph.children_of("c") == ["d"]
    assert graph.roots == ["a"]


def test_graph_rejects_duplicate_span_ids() -> None:
    trace = make_trace([make_span("a", "x", SpanKind.LLM), make_span("a", "y", SpanKind.TOOL)])
    with pytest.raises(GraphError, match="duplicate"):
        build_graph(trace)


def test_graph_rejects_missing_parent() -> None:
    trace = make_trace([make_span("a", "x", SpanKind.LLM, parent="ghost")])
    with pytest.raises(GraphError, match="missing parent"):
        build_graph(trace)


def test_graph_rejects_cycles() -> None:
    trace = make_trace(
        [
            make_span("a", "x", SpanKind.AGENT),
            make_span("b", "y", SpanKind.LLM, parent="a"),
            make_span("c", "z", SpanKind.TOOL, parent="b"),
        ]
    )
    trace.spans[0].parent_span_id = "c"  # hand-crafted cycle (imported trace)
    with pytest.raises(GraphError, match="cycle"):
        build_graph(trace)


def test_logical_trajectory_defaults_to_boundary_kinds() -> None:
    trace = make_trace(
        [
            make_span("a", "agent", SpanKind.AGENT),
            make_span("b", "get_customer", SpanKind.TOOL),
            make_span("c", "GET /x", SpanKind.HTTP),
            make_span("d", "planner", SpanKind.LLM),
        ]
    )
    nodes = logical_trajectory(trace)
    assert [(node.kind, node.name) for node in nodes] == [
        (SpanKind.TOOL, "get_customer"),
        (SpanKind.HTTP, "GET /x"),
        (SpanKind.LLM, "planner"),
    ]


def test_logical_trajectory_kind_filter() -> None:
    trace = make_trace(
        [
            make_span("a", "agent", SpanKind.AGENT),
            make_span("b", "get_customer", SpanKind.TOOL),
            make_span("c", "GET /x", SpanKind.HTTP),
        ]
    )
    nodes = logical_trajectory(trace, kinds={SpanKind.TOOL})
    assert [node.name for node in nodes] == ["get_customer"]


def test_logical_trajectory_nodes_carry_span_ids() -> None:
    trace = make_trace([make_span("b", "get_customer", SpanKind.TOOL)])
    nodes = logical_trajectory(trace)
    assert nodes[0].span_id == "b"
