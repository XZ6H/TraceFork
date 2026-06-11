"""TF-021 / TF-022 / TF-024: span lifecycle, nesting, exception recording."""

import asyncio

import pytest
from tracefork import record, span
from tracefork.models import SpanKind, SpanStatus, TraceStatus


def test_span_within_record() -> None:
    with (
        record("case") as rec,
        span("lookup-customer", kind=SpanKind.TOOL, input={"customer_id": 912}),
    ):
        pass
    (recorded,) = rec.trace.spans
    assert recorded.name == "lookup-customer"
    assert recorded.kind is SpanKind.TOOL
    assert recorded.input == {"customer_id": 912}
    assert recorded.parent_span_id is None
    assert recorded.status is SpanStatus.OK
    assert recorded.completed_at is not None
    assert recorded.span_id.startswith("sp_")


def test_span_defaults_to_custom_kind() -> None:
    with record("case") as rec, span("step"):
        pass
    assert rec.trace.spans[0].kind is SpanKind.CUSTOM


def test_nested_spans_chain_parents() -> None:
    with record("case") as rec, span("agent", kind=SpanKind.AGENT):
        with span("llm", kind=SpanKind.LLM):
            pass
        with span("tool", kind=SpanKind.TOOL), span("http", kind=SpanKind.HTTP):
            pass
    agent, llm, tool, http = rec.trace.spans
    assert llm.parent_span_id == agent.span_id
    assert tool.parent_span_id == agent.span_id
    assert http.parent_span_id == tool.span_id


def test_span_outside_record_is_noop() -> None:
    with span("orphan"):  # must not raise
        pass


def test_span_exception_recorded_and_reraised() -> None:
    with record("case") as rec, pytest.raises(ValueError), span("boom"):
        raise ValueError("kaboom")
    (recorded,) = rec.trace.spans
    assert recorded.status is SpanStatus.ERROR
    assert recorded.error is not None
    assert recorded.error.exception_type == "ValueError"
    assert recorded.error.message == "kaboom"
    assert recorded.completed_at is not None


def test_error_span_persists_no_stack_information() -> None:
    with record("case") as rec, pytest.raises(ValueError), span("boom"):
        raise ValueError("x")
    error = rec.trace.spans[0].error
    assert error is not None
    dumped = error.model_dump()
    assert "traceback" not in dumped
    assert "stack" not in dumped


def test_handled_child_span_error_keeps_trace_completed() -> None:
    with record("case") as rec:
        try:
            with span("failing"):
                raise ValueError("handled")
        except ValueError:
            pass
    assert rec.trace.status is TraceStatus.COMPLETED
    assert rec.trace.spans[0].status is SpanStatus.ERROR


async def test_async_span_within_async_record() -> None:
    async with record("case") as rec, span("inner", kind=SpanKind.LLM):
        await asyncio.sleep(0)
    assert rec.trace.spans[0].name == "inner"
    assert rec.trace.spans[0].status is SpanStatus.OK
