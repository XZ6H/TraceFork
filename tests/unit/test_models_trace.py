"""TF-010: trace domain models — validation, defaults, serialization round trip."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from tracefork.models import Provenance, Span, SpanError, SpanKind, SpanStatus, Trace, TraceStatus

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_span(**overrides: object) -> Span:
    defaults: dict[str, object] = {
        "span_id": "sp_1",
        "parent_span_id": None,
        "kind": SpanKind.LLM,
        "name": "planner",
        "started_at": T0,
    }
    defaults.update(overrides)
    return Span(**defaults)  # type: ignore[arg-type]


def make_trace(**overrides: object) -> Trace:
    defaults: dict[str, object] = {
        "trace_id": "tr_1",
        "name": "customer-refund",
        "started_at": T0,
        "provenance": Provenance(),
    }
    defaults.update(overrides)
    return Trace(**defaults)  # type: ignore[arg-type]


class TestSpan:
    def test_defaults(self) -> None:
        span = make_span()
        assert span.status is SpanStatus.RUNNING
        assert span.completed_at is None
        assert span.output is None
        assert span.error is None
        assert span.attributes == {}
        assert span.input is None

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_span(kind="not-a-kind")

    def test_missing_required_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Span(kind=SpanKind.TOOL)

    def test_empty_ids_and_names_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_span(span_id="")
        with pytest.raises(ValidationError):
            make_span(name="")

    def test_naive_datetime_coerced_to_utc(self) -> None:
        span = make_span(started_at=datetime(2026, 9, 2, 15, 0, 0))
        assert span.started_at.tzinfo is UTC

    def test_non_utc_offset_normalized_to_utc(self) -> None:
        plus_two = timezone(timedelta(hours=2))
        span = make_span(started_at=datetime(2026, 9, 2, 17, 0, 0, tzinfo=plus_two))
        assert span.started_at == T0
        assert span.started_at.tzinfo is UTC

    def test_all_kinds_present(self) -> None:
        assert {kind.value for kind in SpanKind} == {
            "agent",
            "llm",
            "tool",
            "http",
            "retriever",
            "workflow",
            "custom",
        }


class TestTrace:
    def test_defaults(self) -> None:
        trace = make_trace()
        assert trace.schema_version == "1.0"
        assert trace.status is TraceStatus.RUNNING
        assert trace.completed_at is None
        assert trace.input is None
        assert trace.output is None
        assert trace.spans == []
        assert trace.metadata == {}

    def test_empty_trace_is_valid(self) -> None:
        trace = make_trace()
        assert list(trace.spans) == []

    def test_missing_required_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Trace(name="customer-refund", started_at=T0, provenance=Provenance())

    def test_empty_trace_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_trace(trace_id="")

    def test_failed_trace_with_failed_span(self) -> None:
        span = make_span(
            status=SpanStatus.ERROR,
            completed_at=T0,
            error=SpanError(exception_type="ValueError", message="boom"),
        )
        trace = make_trace(spans=[span], status=TraceStatus.FAILED, completed_at=T0)
        assert trace.status is TraceStatus.FAILED
        assert trace.spans[0].error is not None
        assert trace.spans[0].error.exception_type == "ValueError"
        assert trace.spans[0].status is SpanStatus.ERROR

    def test_nested_spans_reference_parents(self) -> None:
        llm = make_span(span_id="sp_1")
        tool = make_span(
            span_id="sp_2", parent_span_id="sp_1", kind=SpanKind.TOOL, name="get_customer"
        )
        trace = make_trace(spans=[llm, tool])
        assert trace.spans[1].parent_span_id == trace.spans[0].span_id


class TestSerializationRoundTrip:
    def test_span_json_round_trip(self) -> None:
        span = make_span(
            status=SpanStatus.OK,
            completed_at=T0,
            input={"customer_id": 912},
            output={"status": "ok"},
            attributes={"region": "eu"},
        )
        restored = Span.model_validate_json(span.model_dump_json())
        assert restored == span

    def test_error_span_json_round_trip(self) -> None:
        span = make_span(
            status=SpanStatus.ERROR,
            completed_at=T0,
            error=SpanError(exception_type="TimeoutError", message="timed out"),
        )
        restored = Span.model_validate_json(span.model_dump_json())
        assert restored == span

    def test_trace_json_round_trip(self) -> None:
        trace = make_trace(
            spans=[
                make_span(),
                make_span(
                    span_id="sp_2",
                    parent_span_id="sp_1",
                    kind=SpanKind.TOOL,
                    name="get_customer",
                    status=SpanStatus.OK,
                    completed_at=T0,
                ),
            ],
            status=TraceStatus.COMPLETED,
            completed_at=T0,
            input="Refund order #31991",
            output="Refund rejected",
            metadata={"suite": "regression"},
        )
        restored = Trace.model_validate_json(trace.model_dump_json())
        assert restored == trace

    def test_unknown_extra_field_rejected(self) -> None:
        data = make_span().model_dump(mode="json")
        data["surprise"] = 1
        with pytest.raises(ValidationError):
            Span.model_validate(data)


class TestProvenance:
    def test_all_fields_optional(self) -> None:
        provenance = Provenance()
        assert provenance.git_commit is None
        assert provenance.git_dirty is None
        assert provenance.git_branch is None

    def test_round_trip(self) -> None:
        provenance = Provenance(git_commit="84aad91", git_dirty=False, git_branch="main")
        restored = Provenance.model_validate_json(provenance.model_dump_json())
        assert restored == provenance


class TestTemporalValidation:
    def test_span_completed_before_started_rejected(self) -> None:
        with pytest.raises(ValidationError, match="completed_at"):
            make_span(completed_at=T0 - timedelta(seconds=1))

    def test_span_completed_at_or_after_started_allowed(self) -> None:
        make_span(completed_at=T0)  # equal is fine (zero-duration span)

    def test_trace_completed_before_started_rejected(self) -> None:
        with pytest.raises(ValidationError, match="completed_at"):
            make_trace(completed_at=T0 - timedelta(seconds=1))

    def test_negative_occurrence_rejected(self) -> None:
        from tracefork.models import BoundaryInvocation

        with pytest.raises(ValidationError):
            BoundaryInvocation(
                boundary_type="tool.python",
                name="x",
                span_id="sp_1",
                occurrence=-1,
            )
