"""TF-110 / TF-111: trace metrics extraction and the cost model abstraction."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tracefork.metrics import TableCostCalculator, extract_metrics
from tracefork.models import BoundaryInvocation, Provenance, Span, SpanKind, Trace

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_trace(invocations: list[BoundaryInvocation], spans: list[Span] | None = None) -> Trace:
    return Trace(
        trace_id="tr_1",
        name="case",
        started_at=T0,
        completed_at=T0 + timedelta(seconds=3),
        provenance=Provenance(),
        spans=spans or [],
        invocations=invocations,
    )


def make_span(kind: SpanKind, name: str, start_s: float, end_s: float) -> Span:
    return Span(
        span_id=f"sp_{name}",
        kind=kind,
        name=name,
        started_at=T0 + timedelta(seconds=start_s),
        completed_at=T0 + timedelta(seconds=end_s),
    )


def make_invocation(boundary_type: str, name: str, metadata: dict[str, Any]) -> BoundaryInvocation:
    return BoundaryInvocation(
        boundary_type=boundary_type,
        name=name,
        span_id="sp_x",
        metadata=metadata,
    )


def test_metrics_counts_calls_by_family() -> None:
    trace = make_trace(
        [
            make_invocation("llm.openai", "gpt-test", {}),
            make_invocation("tool.python", "search", {}),
            make_invocation("tool.python", "fetch", {}),
            make_invocation("http.httpx", "GET /x", {}),
        ]
    )
    metrics = extract_metrics(trace)
    assert metrics.llm_calls == 1
    assert metrics.tool_calls == 2
    assert metrics.http_calls == 1


def test_metrics_sums_tokens_from_invocation_metadata() -> None:
    trace = make_trace(
        [
            make_invocation(
                "llm.openai", "gpt-test", {"usage": {"input_tokens": 100, "output_tokens": 20}}
            ),
            make_invocation(
                "llm.openai", "gpt-test", {"usage": {"input_tokens": 50, "output_tokens": 10}}
            ),
        ]
    )
    metrics = extract_metrics(trace)
    assert metrics.input_tokens == 150
    assert metrics.output_tokens == 30
    assert metrics.total_tokens == 180


def test_metrics_tokens_are_null_without_usage_data() -> None:
    trace = make_trace([make_invocation("llm.openai", "gpt-test", {})])
    metrics = extract_metrics(trace)
    assert metrics.input_tokens is None
    assert metrics.output_tokens is None
    assert metrics.total_tokens is None


def test_metrics_wall_clock_and_latencies() -> None:
    spans = [
        make_span(SpanKind.LLM, "planner", 0.0, 1.0),
        make_span(SpanKind.TOOL, "search", 1.0, 1.5),
        make_span(SpanKind.TOOL, "fetch", 1.5, 2.0),
    ]
    trace = make_trace([], spans=spans)
    metrics = extract_metrics(trace)
    assert metrics.wall_clock_seconds == pytest.approx(3.0)
    assert metrics.llm_latency_ms == 1000
    assert metrics.tool_latency_ms == 1000


def test_table_cost_calculator_known_and_unknown_models() -> None:
    calculator = TableCostCalculator({"gpt-test": (1.0, 2.0)})  # per 1M tokens
    assert calculator.cost_usd("gpt-test", 1_000_000, 500_000) == pytest.approx(2.0)
    assert calculator.cost_usd("unknown-model", 1000, 1000) is None


def test_metrics_estimated_cost_uses_calculator() -> None:
    trace = make_trace(
        [
            make_invocation(
                "llm.openai",
                "gpt-test",
                {"usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}},
            )
        ]
    )
    metrics = extract_metrics(trace, cost_calculator=TableCostCalculator({"gpt-test": (1.0, 2.0)}))
    assert metrics.estimated_cost_usd == pytest.approx(3.0)


def test_metrics_cost_is_null_without_calculator_or_unknown_model() -> None:
    trace = make_trace(
        [
            make_invocation(
                "llm.openai", "mystery", {"usage": {"input_tokens": 1, "output_tokens": 1}}
            )
        ]
    )
    metrics = extract_metrics(trace, cost_calculator=TableCostCalculator({"gpt-test": (1.0, 2.0)}))
    assert metrics.estimated_cost_usd is None
