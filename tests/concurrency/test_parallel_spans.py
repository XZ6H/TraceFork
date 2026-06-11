"""TF-023: parallel span support — common parent, start-order stability.

Sibling order in the trace must follow start order, never completion order,
and parallel tasks must inherit the enclosing span as their common parent.
"""

import asyncio

import pytest
from tracefork import record, span
from tracefork.models import SpanKind, SpanStatus


async def _child(name: str, kind: SpanKind = SpanKind.TOOL, delay: float = 0.0) -> None:
    with span(name, kind=kind):
        await asyncio.sleep(delay)


async def test_gather_children_share_parent() -> None:
    async with record("case") as rec:
        with span("agent", kind=SpanKind.AGENT):
            # c2 completes first, but c1 must still appear first (start order).
            await asyncio.gather(_child("c1", delay=0.05), _child("c2", delay=0.005))

    agent = rec.trace.spans[0]
    children = rec.trace.spans[1:]
    assert agent.name == "agent"
    assert [child.name for child in children] == ["c1", "c2"]
    for child in children:
        assert child.parent_span_id == agent.span_id
        assert child.status is SpanStatus.OK


async def test_gather_roots_have_no_parent() -> None:
    async with record("case") as rec:
        await asyncio.gather(_child("a"), _child("b"), _child("c"))

    roots = rec.trace.spans
    assert [root.name for root in roots] == ["a", "b", "c"]
    assert all(root.parent_span_id is None for root in roots)


async def test_exception_in_gather_task_is_recorded() -> None:
    async def failing() -> None:
        with span("failing"):
            raise ValueError("task failed")

    with record("case") as rec, pytest.raises(ValueError):
        await asyncio.gather(_child("healthy"), failing())

    statuses = {recorded.name: recorded.status for recorded in rec.trace.spans}
    assert statuses["healthy"] is SpanStatus.OK
    assert statuses["failing"] is SpanStatus.ERROR


async def test_deeply_nested_parallel_tasks_inherit_inner_span() -> None:
    async with record("case") as rec:
        with span("outer"):
            with span("inner"):
                await asyncio.gather(_child("leaf1"), _child("leaf2"))

    by_name = {recorded.name: recorded for recorded in rec.trace.spans}
    inner = by_name["inner"]
    for leaf in ("leaf1", "leaf2"):
        assert by_name[leaf].parent_span_id == inner.span_id
