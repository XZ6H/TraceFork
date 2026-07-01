"""Execution graph and logical trajectory (TF-120..122, ADR 0005).

The graph is derived from span parentage, never stored separately. Sibling
order is span start order. Loading validates the structure so corrupted or
malformed imported traces fail with precise errors.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracefork.errors import GraphError
from tracefork.models import Span, SpanKind, Trace

DEFAULT_TRAJECTORY_KINDS = frozenset({SpanKind.LLM, SpanKind.TOOL, SpanKind.HTTP})


@dataclass(frozen=True)
class TrajectoryNode:
    """One simplified step of a logical trajectory."""

    kind: SpanKind
    name: str
    span_id: str


class ExecutionGraph:
    """Span tree derived from a trace's parent relationships."""

    def __init__(
        self,
        nodes: dict[str, Span],
        children: dict[str | None, list[str]],
        roots: list[str],
    ) -> None:
        self.nodes = nodes
        self._children = children
        self.roots = roots

    def children_of(self, span_id: str) -> list[str]:
        return list(self._children.get(span_id, []))


def build_graph(trace: Trace) -> ExecutionGraph:
    """Derive the execution graph, validating structure (TF-121)."""
    nodes: dict[str, Span] = {}
    for span in trace.spans:
        if span.span_id in nodes:
            msg = f"duplicate span id {span.span_id!r}"
            raise GraphError(msg)
        nodes[span.span_id] = span

    children: dict[str | None, list[str]] = {}
    roots: list[str] = []
    for span in trace.spans:
        if span.parent_span_id is not None and span.parent_span_id not in nodes:
            msg = f"span {span.span_id!r} references missing parent {span.parent_span_id!r}"
            raise GraphError(msg)
        children.setdefault(span.parent_span_id, []).append(span.span_id)
        if span.parent_span_id is None:
            roots.append(span.span_id)

    for span_id, span in nodes.items():
        _ensure_acyclic(nodes, span_id, span)

    return ExecutionGraph(nodes=nodes, children=children, roots=roots)


def _ensure_acyclic(nodes: dict[str, Span], span_id: str, span: Span) -> None:
    seen = {span_id}
    current = span.parent_span_id
    while current is not None:
        if current in seen:
            msg = f"cycle detected at span {span_id!r}"
            raise GraphError(msg)
        seen.add(current)
        current = nodes[current].parent_span_id


def logical_trajectory(
    trace: Trace, *, kinds: frozenset[SpanKind] | set[SpanKind] | None = None
) -> list[TrajectoryNode]:
    """Flatten a trace into a logical trajectory of boundary kinds (TF-122).

    Spans are stored in start order (pre-order by construction), so the
    flattened list preserves hierarchical ordering.
    """
    allowed = DEFAULT_TRAJECTORY_KINDS if kinds is None else kinds
    return [
        TrajectoryNode(kind=span.kind, name=span.name, span_id=span.span_id)
        for span in trace.spans
        if span.kind in allowed
    ]
