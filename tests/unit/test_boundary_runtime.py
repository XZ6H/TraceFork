"""TF-030 / TF-032 / TF-033: boundary runtime, execution context, invocation recording.

Key invariants pinned here:

- NORMAL mode never records.
- RECORD mode calls live exactly once and records a full invocation.
- REPLAY mode without a session fails closed: the live call never happens.
"""

from typing import Any

import pytest
from tracefork import record, span
from tracefork.boundaries import (
    BoundaryRegistry,
    BoundaryRuntime,
    ExecutionContext,
    ExecutionMode,
    execution_context,
)
from tracefork.boundaries.errors import UnknownBoundaryError
from tracefork.errors import AdapterError, ReplayError
from tracefork.models import SpanKind, SpanStatus


def _counting_live(calls: list[int], result: Any = "live-result") -> Any:
    async def live() -> Any:
        calls.append(1)
        return result

    return live


async def test_normal_mode_passes_through_without_recording(
    echo_registry: BoundaryRegistry,
) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)
    calls: list[int] = []
    result = await runtime.invoke("tool.echo", "search", {"q": "x"}, _counting_live(calls))
    assert result == "live-result"
    assert calls == [1]


async def test_normal_mode_inside_recording_records_nothing(
    echo_registry: BoundaryRegistry,
) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)
    calls: list[int] = []
    with record("case") as rec:
        context = ExecutionContext(mode=ExecutionMode.NORMAL, recording=rec)
        async with execution_context(context):
            await runtime.invoke("tool.echo", "search", {"q": "x"}, _counting_live(calls))
    assert calls == [1]
    assert rec.trace.invocations == []
    assert rec.trace.spans == []


async def test_record_mode_records_invocation_and_boundary_span(
    echo_registry: BoundaryRegistry,
) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)
    calls: list[int] = []
    with record("case") as rec, span("agent", kind=SpanKind.AGENT):
        result = await runtime.invoke(
            "tool.echo",
            "search_orders",
            {"customer_id": 912},
            _counting_live(calls, {"orders": []}),
        )
    assert result == {"orders": []}
    assert calls == [1]

    agent_span, boundary_span = rec.trace.spans
    assert boundary_span.kind is SpanKind.TOOL
    assert boundary_span.name == "search_orders"
    assert boundary_span.parent_span_id == agent_span.span_id
    assert boundary_span.status is SpanStatus.OK
    assert boundary_span.output == {"orders": []}

    (invocation,) = rec.trace.invocations
    assert invocation.boundary_type == "tool.echo"
    assert invocation.name == "search_orders"
    assert invocation.request == {"customer_id": 912}
    assert invocation.response == {"orders": []}
    assert invocation.span_id == boundary_span.span_id
    assert invocation.parent_span_id == agent_span.span_id
    assert invocation.parent_name == "agent"
    # Fingerprints are stable 64-char hex strings (canonicalization, TF-042).
    assert invocation.fingerprint is not None
    assert len(invocation.fingerprint) == 64
    assert invocation.occurrence == 0
    assert invocation.metadata["echo"] is True


async def test_identical_calls_share_fingerprint_and_get_occurrences(
    echo_registry: BoundaryRegistry,
) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)
    with record("case") as rec:
        await runtime.invoke("tool.echo", "search", {"q": 1}, _counting_live([]))
        await runtime.invoke("tool.echo", "search", {"q": 1}, _counting_live([]))
    first, second = rec.trace.invocations
    assert first.fingerprint == second.fingerprint
    assert (first.occurrence, second.occurrence) == (0, 1)


async def test_non_canonicalizable_request_fails_before_any_recording(
    echo_registry: BoundaryRegistry,
) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)

    class Mystery:
        pass

    calls: list[int] = []

    async def live() -> None:
        calls.append(1)

    with record("case") as rec, pytest.raises(AdapterError):
        await runtime.invoke("tool.echo", "search", {"k": Mystery()}, live)
    assert calls == []  # never executed
    assert rec.trace.invocations == []
    assert rec.trace.spans == []


async def test_record_mode_without_enclosing_span_links_to_root(
    echo_registry: BoundaryRegistry,
) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)
    with record("case") as rec:
        await runtime.invoke("tool.echo", "search", {}, _counting_live([]))
    (boundary_span,) = rec.trace.spans
    (invocation,) = rec.trace.invocations
    assert invocation.parent_span_id is None
    assert invocation.span_id == boundary_span.span_id


async def test_repeated_calls_get_occurrence_index(echo_registry: BoundaryRegistry) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)
    with record("case") as rec:
        await runtime.invoke("tool.echo", "search", {"q": 1}, _counting_live([]))
        await runtime.invoke("tool.echo", "search", {"q": 1}, _counting_live([]))
        await runtime.invoke("tool.echo", "other", {"q": 3}, _counting_live([]))
    assert [invocation.occurrence for invocation in rec.trace.invocations] == [0, 1, 0]


async def test_llm_boundary_type_maps_to_llm_span(echo_registry: BoundaryRegistry) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)
    with record("case") as rec:
        await runtime.invoke("llm.test", "planner", {"messages": []}, _counting_live([]))
    assert rec.trace.spans[0].kind is SpanKind.LLM


async def test_boundary_error_records_error_span_and_failed_invocation(
    echo_registry: BoundaryRegistry,
) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)

    async def live() -> Any:
        raise ValueError("tool exploded")

    with record("case") as rec, pytest.raises(ValueError), span("outer"):
        await runtime.invoke("tool.echo", "boom", {"x": 1}, live)

    outer_span, boundary_span = rec.trace.spans
    assert boundary_span.status is SpanStatus.ERROR
    assert boundary_span.error is not None
    assert boundary_span.error.exception_type == "ValueError"

    (invocation,) = rec.trace.invocations
    assert invocation.response is None
    assert invocation.metadata["error"] == {"type": "ValueError", "message": "tool exploded"}
    assert invocation.parent_span_id == outer_span.span_id


async def test_replay_mode_without_session_fails_closed(
    echo_registry: BoundaryRegistry,
) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)
    calls: list[int] = []
    context = ExecutionContext(mode=ExecutionMode.REPLAY)
    async with execution_context(context):
        with pytest.raises(ReplayError):
            await runtime.invoke("tool.echo", "search", {"q": "x"}, _counting_live(calls))
    assert calls == []  # the live call must never happen


async def test_unknown_boundary_type_fails_before_recording() -> None:
    runtime = BoundaryRuntime(registry=BoundaryRegistry())
    with record("case") as rec, pytest.raises(UnknownBoundaryError):
        await runtime.invoke("tool.nope", "search", {}, _counting_live([]))
    assert rec.trace.invocations == []
    assert rec.trace.spans == []


class WrappingHandler:
    """Handler whose native form differs from its canonical payload (TF-073)."""

    async def execute(self, request, call_live):
        from tracefork.models import BoundaryResponse

        return BoundaryResponse(response=await call_live(), metadata={})

    def restore(self, response, metadata):
        return {"native": response}


async def test_record_mode_returns_restored_native_response() -> None:
    from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime

    registry = BoundaryRegistry()
    registry.register("llm.test", WrappingHandler())
    runtime = BoundaryRuntime(registry=registry)

    with record("case") as rec:
        native = await runtime.invoke("llm.test", "planner", {"a": 1}, _counting_live([]))

    assert native == {"native": "live-result"}  # caller gets the native shape
    assert rec.trace.invocations[0].response == "live-result"  # canonical stored
    assert rec.trace.spans[0].output == "live-result"
