"""TF-020: recording context — record(), contextvars, async support."""

import asyncio

import pytest
from tracefork import record
from tracefork.models import TraceStatus


def test_record_yields_recording_with_running_trace() -> None:
    with record("case") as rec:
        assert rec.trace.name == "case"
        assert rec.trace.trace_id.startswith("tr_")
        assert rec.trace.status is TraceStatus.RUNNING
        assert rec.trace.started_at is not None
    assert rec.trace.status is TraceStatus.COMPLETED
    assert rec.trace.completed_at is not None


def test_record_sets_input_and_metadata() -> None:
    with record("case", input={"q": "refund"}, metadata={"suite": "x"}) as rec:
        pass
    assert rec.trace.input == {"q": "refund"}
    assert rec.trace.metadata == {"suite": "x"}


def test_record_captures_provenance() -> None:
    with record("case") as rec:
        pass
    # Tests run inside the TraceFork repository, so git is always available.
    assert rec.trace.provenance.git_commit is not None
    assert rec.trace.provenance.git_dirty is not None
    assert rec.trace.provenance.git_branch is not None


def test_record_without_provenance_capture() -> None:
    with record("case", capture_provenance=False) as rec:
        pass
    assert rec.trace.provenance == rec.trace.provenance.__class__()


def test_record_body_exception_fails_trace() -> None:
    with pytest.raises(RuntimeError), record("case") as rec:
        raise RuntimeError("boom")
    assert rec.trace.status is TraceStatus.FAILED
    assert rec.trace.completed_at is not None


def test_trace_ids_are_unique_across_recordings() -> None:
    with record("outer") as outer, record("inner") as inner:
        pass
    assert outer.trace.trace_id != inner.trace.trace_id
    assert outer.trace.name == "outer"
    assert inner.trace.name == "inner"


async def test_async_record() -> None:
    async with record("async-case") as rec:
        await asyncio.sleep(0)
        assert rec.trace.status is TraceStatus.RUNNING
    assert rec.trace.status is TraceStatus.COMPLETED


async def test_async_record_body_exception_fails_trace() -> None:
    with pytest.raises(ValueError):
        async with record("case") as rec:
            raise ValueError("nope")
    assert rec.trace.status is TraceStatus.FAILED
