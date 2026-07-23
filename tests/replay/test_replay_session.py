"""TF-050..054: hermetic replay — sessions, fail-closed matching, result reports.

The core invariants:

- REPLAY boundaries return recorded responses; the live call never runs.
- An unmatched call raises ReplayMismatchError with diagnostics.
- The session reports matched/unmatched/unused/live counts.
- Hermetic runs report zero live boundaries and zero network calls.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from tracefork import record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime, ReplayMode, ReplayPolicy
from tracefork.canonicalization import Canonicalizer
from tracefork.errors import (
    ReplayError,
    ReplayMismatchError,
    ReplayPolicyError,
    ReplayRecordedError,
)
from tracefork.models import SpanKind, SpanStatus
from tracefork.replay import ReplaySession
from tracefork.serialization import FixtureEnvelope, build_envelope

from tests.conftest import EchoHandler

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_registry() -> BoundaryRegistry:
    registry = BoundaryRegistry()
    registry.register("tool.echo", EchoHandler())
    registry.register("llm.echo", EchoHandler())
    return registry


def live_counter(results: list[Any]) -> Any:
    """Build a live callable that appends to *results* when executed."""

    async def live() -> Any:
        marker = object()
        results.append(marker)
        return f"live-{len(results)}"

    return live


async def record_two_tool_calls() -> FixtureEnvelope:
    runtime = BoundaryRuntime(registry=make_registry())
    with record("refund-case") as rec:
        await runtime.invoke("tool.echo", "search_orders", {"customer_id": 912}, live_counter([]))
        await runtime.invoke("tool.echo", "refund_order", {"order_id": 31991}, live_counter([]))
    return build_envelope(rec.trace, created_at=T0)


async def record_llm_and_tool_calls() -> FixtureEnvelope:
    runtime = BoundaryRuntime(registry=make_registry())
    with record("mixed-case") as rec:
        await runtime.invoke("llm.echo", "planner", {"messages": ["hi"]}, live_counter([]))
        await runtime.invoke("tool.echo", "search_orders", {"customer_id": 912}, live_counter([]))
    return build_envelope(rec.trace, created_at=T0)


async def test_hermetic_replay_never_calls_live(echo_registry: BoundaryRegistry) -> None:
    fixture = await record_two_tool_calls()
    runtime = BoundaryRuntime(registry=echo_registry)
    live_results: list[Any] = []

    session = ReplaySession(fixture=fixture, registry=echo_registry)
    with session:
        first = await runtime.invoke(
            "tool.echo", "search_orders", {"customer_id": 912}, live_counter(live_results)
        )
        second = await runtime.invoke(
            "tool.echo", "refund_order", {"order_id": 31991}, live_counter(live_results)
        )

    assert first == "live-1"  # recorded response, not a new live call
    assert second == "live-1"  # each recorded response is served verbatim
    assert live_results == []  # the real functions never ran

    result = session.result
    assert result.status == "passed"
    assert result.matched == 2
    assert result.unexpected == []
    assert result.unused_recordings == []
    assert result.live_boundaries == []
    assert result.network_calls == 0
    assert result.is_hermetic is True


async def test_replayed_boundary_spans_land_in_candidate_trace(
    echo_registry: BoundaryRegistry,
) -> None:
    fixture = await record_two_tool_calls()
    runtime = BoundaryRuntime(registry=echo_registry)

    session = ReplaySession(fixture=fixture, registry=echo_registry)
    with session:
        await runtime.invoke("tool.echo", "search_orders", {"customer_id": 912}, live_counter([]))

    trace = session.result.trace
    assert trace.name == "refund-case"
    (span,) = trace.spans
    assert span.kind is SpanKind.TOOL
    assert span.name == "search_orders"
    assert span.output == "live-1"
    assert span.attributes["replay"] == "replayed"

    (invocation,) = trace.invocations
    assert invocation.response == "live-1"
    assert invocation.span_id == span.span_id
    assert invocation.metadata["replay"] == "replayed"


async def test_mismatch_fails_closed_with_diagnostics(
    echo_registry: BoundaryRegistry,
) -> None:
    fixture = await record_two_tool_calls()
    runtime = BoundaryRuntime(registry=echo_registry)
    live_results: list[Any] = []

    session = ReplaySession(fixture=fixture, registry=echo_registry)
    with session, pytest.raises(ReplayMismatchError) as excinfo:
        await runtime.invoke(
            "tool.echo",
            "search_orders",
            {"customer_id": 912, "region": "EU"},
            live_counter(live_results),
        )

    assert live_results == []  # fail closed: no live call escaped
    assert "region" in str(excinfo.value)
    assert "search_orders" in str(excinfo.value)

    result = session.result
    assert result.status == "failed"
    assert len(result.unexpected) == 1
    assert result.matched == 0


async def test_unused_recordings_are_reported(echo_registry: BoundaryRegistry) -> None:
    fixture = await record_two_tool_calls()
    runtime = BoundaryRuntime(registry=echo_registry)

    session = ReplaySession(fixture=fixture, registry=echo_registry)
    with session:
        await runtime.invoke("tool.echo", "search_orders", {"customer_id": 912}, live_counter([]))

    result = session.result
    assert result.status == "passed"  # unused recordings are warnings, not failures
    assert len(result.unused_recordings) == 1
    assert result.unused_recordings[0].name == "refund_order"


async def test_live_llm_with_replayed_tools() -> None:
    registry = make_registry()
    fixture = await record_llm_and_tool_calls()
    runtime = BoundaryRuntime(registry=registry)
    llm_results: list[Any] = []
    tool_results: list[Any] = []
    policy = ReplayPolicy(llm=ReplayMode.LIVE)

    session = ReplaySession(fixture=fixture, registry=registry, policy=policy)
    with session:
        llm = await runtime.invoke(
            "llm.echo", "planner", {"messages": ["hi"]}, live_counter(llm_results)
        )
        tool = await runtime.invoke(
            "tool.echo", "search_orders", {"customer_id": 912}, live_counter(tool_results)
        )

    assert llm == "live-1"  # actually executed live
    assert llm_results != []  # the live function ran
    assert tool == "live-1"  # served from the recording
    assert tool_results == []

    result = session.result
    assert result.status == "passed"
    assert result.live_boundaries == ["llm.echo.planner"]
    assert result.network_calls == 1
    assert result.is_hermetic is False


async def test_exact_tool_override(echo_registry: BoundaryRegistry) -> None:
    fixture = await record_two_tool_calls()
    runtime = BoundaryRuntime(registry=echo_registry)
    live_results: list[Any] = []
    policy = ReplayPolicy(tools={"search_orders": ReplayMode.LIVE})

    session = ReplaySession(fixture=fixture, registry=echo_registry, policy=policy)
    with session:
        await runtime.invoke(
            "tool.echo", "search_orders", {"customer_id": 912}, live_counter(live_results)
        )
        await runtime.invoke("tool.echo", "refund_order", {"order_id": 31991}, live_counter([]))

    assert live_results != []  # search_orders ran live
    result = session.result
    assert result.live_boundaries == ["tool.echo.search_orders"]
    assert result.matched == 1


async def test_repeated_identical_calls_match_in_occurrence_order(
    echo_registry: BoundaryRegistry,
) -> None:
    runtime = BoundaryRuntime(registry=echo_registry)
    with record("repeat-case") as rec:
        await runtime.invoke("tool.echo", "search", {"q": "x"}, live_counter([]))
        await runtime.invoke("tool.echo", "search", {"q": "x"}, live_counter([]))
    fixture = build_envelope(rec.trace, created_at=T0)

    session = ReplaySession(fixture=fixture, registry=echo_registry)
    with session:
        first = await runtime.invoke("tool.echo", "search", {"q": "x"}, live_counter([]))
        second = await runtime.invoke("tool.echo", "search", {"q": "x"}, live_counter([]))

    assert (first, second) == ("live-1", "live-1")
    assert session.result.matched == 2
    assert session.result.unused_recordings == []


async def test_mock_mode_not_supported_yet_fails_closed(
    echo_registry: BoundaryRegistry,
) -> None:
    fixture = await record_two_tool_calls()
    runtime = BoundaryRuntime(registry=echo_registry)
    live_results: list[Any] = []
    policy = ReplayPolicy(default=ReplayMode.MOCK)

    session = ReplaySession(fixture=fixture, registry=echo_registry, policy=policy)
    with session, pytest.raises(ReplayPolicyError):
        await runtime.invoke(
            "tool.echo", "search_orders", {"customer_id": 912}, live_counter(live_results)
        )
    assert live_results == []


async def test_restore_translates_recorded_payload_to_native(
    echo_registry: BoundaryRegistry,
) -> None:
    class NativeHandler(EchoHandler):
        def restore(self, response: Any, metadata: dict[str, Any]) -> Any:
            return {"native": response}

    registry = BoundaryRegistry()
    registry.register("tool.echo", NativeHandler())

    record_runtime = BoundaryRuntime(registry=registry)
    with record("case") as rec:
        await record_runtime.invoke("tool.echo", "fetch", {"id": 1}, live_counter([]))
    fixture = build_envelope(rec.trace, created_at=T0)

    replay_runtime = BoundaryRuntime(registry=registry)
    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        native = await replay_runtime.invoke("tool.echo", "fetch", {"id": 1}, live_counter([]))
    assert native == {"native": "live-1"}


async def test_exception_inside_block_marks_result_failed(
    echo_registry: BoundaryRegistry,
) -> None:
    fixture = await record_two_tool_calls()
    runtime = BoundaryRuntime(registry=echo_registry)

    session = ReplaySession(fixture=fixture, registry=echo_registry)
    with pytest.raises(RuntimeError), session:
        await runtime.invoke("tool.echo", "search_orders", {"customer_id": 912}, live_counter([]))
        raise RuntimeError("application code failed")

    assert session.result.status == "failed"
    assert session.result.trace.status == "failed"


async def test_session_requires_registry_entry_to_restore(echo_registry: BoundaryRegistry) -> None:
    from tracefork.boundaries.errors import UnknownBoundaryError

    fixture = await record_two_tool_calls()
    runtime = BoundaryRuntime(registry=echo_registry)
    empty_registry = BoundaryRegistry()
    canonicalizer = Canonicalizer()

    session = ReplaySession(fixture=fixture, registry=empty_registry, canonicalizer=canonicalizer)
    with session, pytest.raises(UnknownBoundaryError):
        await runtime.invoke("tool.echo", "search_orders", {"customer_id": 912}, live_counter([]))


async def test_parallel_identical_replays_match_distinct_occurrences(
    echo_registry: BoundaryRegistry,
) -> None:
    """Plan §34 concurrency: repeated identical calls replay stably under gather."""
    runtime = BoundaryRuntime(registry=echo_registry)
    with record("parallel-replay") as rec:
        for _ in range(5):
            await runtime.invoke("tool.echo", "search", {"q": "x"}, live_counter([]))
    fixture = build_envelope(rec.trace, created_at=T0)

    replay_runtime = BoundaryRuntime(registry=echo_registry)
    session = ReplaySession(fixture=fixture, registry=echo_registry)

    async def replay_one() -> str:
        async def live() -> str:
            raise AssertionError("must not run")

        return await replay_runtime.invoke("tool.echo", "search", {"q": "x"}, live)

    with session:
        outputs = await asyncio.gather(*(replay_one() for _ in range(5)))

    assert outputs == ["live-1"] * 5
    assert session.result.matched == 5
    assert session.result.unused_recordings == []


async def test_live_boundary_that_raises_still_counted_live(
    echo_registry: BoundaryRegistry,
) -> None:
    fixture = await record_two_tool_calls()
    runtime = BoundaryRuntime(registry=echo_registry)
    policy = ReplayPolicy(tools={"search_orders": ReplayMode.LIVE})

    async def live() -> Any:
        raise ValueError("live dependency down")

    session = ReplaySession(fixture=fixture, registry=echo_registry, policy=policy)
    with session, pytest.raises(ValueError):
        await runtime.invoke("tool.echo", "search_orders", {"customer_id": 912}, live)

    result = session.result
    assert result.live_boundaries == ["tool.echo.search_orders"]
    assert result.is_hermetic is False
    # The failed live call is recorded with its error, like any boundary.
    (invocation,) = result.trace.invocations
    assert invocation.metadata["error"]["type"] == "ValueError"


def test_result_before_enter_rejected(echo_registry: BoundaryRegistry) -> None:
    fixture = build_envelope(_trace_stub(), created_at=T0)
    session = ReplaySession(fixture=fixture, registry=echo_registry)
    with pytest.raises(ReplayError, match="has not run"):
        _ = session.result


def _trace_stub() -> Any:
    from tracefork.models import Provenance, Trace

    return Trace(trace_id="tr_1", name="case", started_at=T0, provenance=Provenance())


async def test_recorded_error_replays_as_replay_recorded_error(
    echo_registry: BoundaryRegistry,
) -> None:
    """A boundary that failed during recording must replay as the recorded
    failure — not as a silent None (error-replay semantics)."""
    runtime = BoundaryRuntime(registry=echo_registry)

    async def live() -> Any:
        raise ValueError("recorded failure")

    with record("error-replay") as rec, pytest.raises(ValueError):
        await runtime.invoke("tool.echo", "flaky", {"q": 1}, live)
    fixture = build_envelope(rec.trace, created_at=T0)

    replay_runtime = BoundaryRuntime(registry=echo_registry)

    async def live_must_not_run() -> Any:
        raise AssertionError("live must never run during replay")

    session = ReplaySession(fixture=fixture, registry=echo_registry)
    # The exception must ESCAPE the session (as in real usage) so the replay
    # is marked failed; pytest.raises sits outside the session context.
    with pytest.raises(ReplayRecordedError) as excinfo, session:
        await replay_runtime.invoke("tool.echo", "flaky", {"q": 1}, live_must_not_run)

    assert "ValueError" in str(excinfo.value)
    assert "recorded failure" in str(excinfo.value)
    result = session.result
    assert result.matched == 1
    assert result.status == "failed"  # the replay reproduced the recorded error
    (candidate_invocation,) = result.trace.invocations
    assert candidate_invocation.response is None
    (candidate_span,) = result.trace.spans
    assert candidate_span.status is SpanStatus.ERROR
    assert candidate_span.error is not None
    assert candidate_span.error.exception_type == "ValueError"
    assert candidate_span.attributes["replay"] == "replayed-error"
