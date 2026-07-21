"""Recording edge cases: leaked spans, cancellation, task isolation, idempotency."""

import asyncio

import pytest

from tracefork import record, span
from tracefork.models import SpanKind, SpanStatus, TraceStatus
from tracefork.recording.recorder import Recording


async def test_leaked_span_is_swept_to_error_on_finish() -> None:
    # Runs in a task so the never-reset contextvar cannot leak into other tests.
    async def leak() -> "Recording":
        with record("case") as rec:
            ctx = span("leaked", kind=SpanKind.TOOL)
            ctx.__enter__()  # user bug: span opened but never exited
        return rec, ctx  # noqa: B023

    rec, ctx = await asyncio.create_task(leak())
    (leaked,) = rec.trace.spans
    assert leaked.status is SpanStatus.ERROR
    assert leaked.error is not None
    assert "still open" in leaked.error.message
    assert rec.trace.status is TraceStatus.COMPLETED

    # A late context exit after finish() must not resurrect the span.
    ctx.__exit__(None, None, None)
    assert leaked.status is SpanStatus.ERROR


def test_finish_is_idempotent() -> None:
    with record("case") as rec:
        pass
    first = rec.trace.completed_at
    rec.finish()
    assert rec.trace.completed_at == first
    assert rec.trace.status is TraceStatus.COMPLETED


async def test_cancelled_task_records_cancelled_error() -> None:
    async def long_tool() -> str:
        with span("slow-tool", kind=SpanKind.TOOL):
            await asyncio.sleep(30)
            return "never"

    with record("case") as rec:
        task = asyncio.create_task(long_tool())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    (tool_span,) = rec.trace.spans
    assert tool_span.name == "slow-tool"
    assert tool_span.status is SpanStatus.ERROR
    assert tool_span.error is not None
    assert tool_span.error.exception_type == "CancelledError"


async def test_nested_recording_is_isolated_across_tasks() -> None:
    with record("outer") as outer:

        async def inner_task() -> None:
            with record("inner") as inner, span("inner-span"):
                await asyncio.sleep(0)
                assert inner is not None
            inner.trace  # keep reference

        await asyncio.create_task(inner_task())
        with span("outer-span"):
            await asyncio.sleep(0)

    assert [s.name for s in outer.trace.spans] == ["outer-span"]
    # The inner recording is not reachable through the outer trace; task-level
    # record() replaced the context only inside the task.
    from tracefork.recording.context import current_recording

    assert current_recording.get() is None


def test_span_after_recording_closed_is_noop() -> None:
    with record("case") as rec:
        pass
    with span("late"):  # no active recording — must not raise or record
        pass
    assert [s.name for s in rec.trace.spans] == []


def test_recording_metadata_is_copied_not_aliased() -> None:
    metadata = {"key": "value"}
    with record("case", metadata=metadata) as rec:
        pass
    metadata["key"] = "mutated"
    assert rec.trace.metadata == {"key": "value"}
