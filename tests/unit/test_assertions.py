"""TF-140..145: assertions — require/forbid tools, ordering, call and resource limits."""

from datetime import UTC, datetime
from typing import Any

from tracefork.assertions import (
    ResourceMaximums,
    ToolExpectations,
    TraceExpectations,
    evaluate_expectations,
)
from tracefork.metrics import TableCostCalculator
from tracefork.models import BoundaryInvocation, Provenance, Span, SpanKind, Trace

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_trace(
    tool_calls: list[str], llm_calls: int = 1, usage: dict[str, Any] | None = None
) -> Trace:
    spans = [Span(span_id="sp_root", kind=SpanKind.AGENT, name="agent", started_at=T0)]
    invocations = [
        BoundaryInvocation(boundary_type="tool.python", name=name, span_id="sp_root")
        for name in tool_calls
    ]
    invocations.extend(
        BoundaryInvocation(
            boundary_type="llm.openai",
            name="gpt-test",
            span_id="sp_root",
            metadata={"usage": usage} if usage else {},
        )
        for _ in range(llm_calls)
    )
    return Trace(
        trace_id="tr_1",
        name="case",
        started_at=T0,
        provenance=Provenance(),
        spans=spans,
        invocations=invocations,
    )


def by_name(results: list) -> dict:
    return {result.name: result for result in results}


def test_required_tool_present_and_absent() -> None:
    trace = make_trace(["get_customer", "check_refund_policy"])
    expectations = TraceExpectations(tools=ToolExpectations(require=["get_customer"]))
    results = by_name(evaluate_expectations(trace, expectations))
    assert results["required tool get_customer"].passed

    missing = TraceExpectations(tools=ToolExpectations(require=["check_refund_policy"]))
    results = by_name(evaluate_expectations(make_trace(["get_customer"]), missing))
    assert not results["required tool check_refund_policy"].passed


def test_forbidden_tool_violation() -> None:
    trace = make_trace(["get_customer", "delete_customer"])
    expectations = TraceExpectations(tools=ToolExpectations(forbid=["delete_customer"]))
    results = by_name(evaluate_expectations(trace, expectations))
    assert not results["forbidden tool delete_customer"].passed

    clean = make_trace(["get_customer"])
    results = by_name(evaluate_expectations(clean, expectations))
    assert results["forbidden tool delete_customer"].passed


def test_ordering_allows_interleaved_calls() -> None:
    trace = make_trace(
        [
            "get_customer",
            "search",
            "check_refund_policy",
            "search",
            "refund_customer",
        ]
    )
    expectations = TraceExpectations(before=[("check_refund_policy", "refund_customer")])
    results = by_name(evaluate_expectations(trace, expectations))
    assert results["ordering check_refund_policy before refund_customer"].passed


def test_ordering_fails_when_reversed() -> None:
    trace = make_trace(["refund_customer", "check_refund_policy"])
    expectations = TraceExpectations(before=[("check_refund_policy", "refund_customer")])
    results = by_name(evaluate_expectations(trace, expectations))
    assert not results["ordering check_refund_policy before refund_customer"].passed


def test_ordering_fails_when_required_tool_missing() -> None:
    trace = make_trace(["refund_customer"])
    expectations = TraceExpectations(before=[("check_refund_policy", "refund_customer")])
    results = by_name(evaluate_expectations(trace, expectations))
    assert not results["ordering check_refund_policy before refund_customer"].passed


def test_max_calls_per_tool() -> None:
    trace = make_trace(["search", "search", "search"])
    expectations = TraceExpectations(max_calls={"search": 3})
    results = by_name(evaluate_expectations(trace, expectations))
    assert results["max calls for search"].passed

    strict = TraceExpectations(max_calls={"search": 2})
    results = by_name(evaluate_expectations(trace, strict))
    assert not results["max calls for search"].passed


def test_max_tool_and_llm_calls() -> None:
    trace = make_trace(["a", "b"], llm_calls=2)
    expectations = TraceExpectations(max=ResourceMaximums(tool_calls=3, llm_calls=2))
    results = by_name(evaluate_expectations(trace, expectations))
    assert results["max tool calls"].passed
    assert results["max llm calls"].passed

    strict = TraceExpectations(max=ResourceMaximums(tool_calls=1))
    results = by_name(evaluate_expectations(trace, strict))
    assert not results["max tool calls"].passed


def test_max_total_tokens() -> None:
    trace = make_trace([], usage={"input_tokens": 800, "output_tokens": 200})
    expectations = TraceExpectations(max=ResourceMaximums(total_tokens=1000))
    results = by_name(evaluate_expectations(trace, expectations))
    assert results["max total tokens"].passed

    strict = TraceExpectations(max=ResourceMaximums(total_tokens=999))
    results = by_name(evaluate_expectations(trace, strict))
    assert not results["max total tokens"].passed


def test_max_total_tokens_without_usage_data_passes_with_evidence() -> None:
    trace = make_trace(["a"])
    expectations = TraceExpectations(max=ResourceMaximums(total_tokens=100))
    results = by_name(evaluate_expectations(trace, expectations))
    assert results["max total tokens"].passed
    assert "no token data" in results["max total tokens"].detail


def test_max_cost_with_calculator() -> None:
    trace = make_trace([], usage={"input_tokens": 1_000_000, "output_tokens": 0})
    calculator = TableCostCalculator({"gpt-test": (1.0, 0.0)})
    expectations = TraceExpectations(max=ResourceMaximums(cost_usd=2.0))
    results = by_name(evaluate_expectations(trace, expectations, cost_calculator=calculator))
    assert results["max cost"].passed

    strict = TraceExpectations(max=ResourceMaximums(cost_usd=0.5))
    results = by_name(evaluate_expectations(trace, strict, cost_calculator=calculator))
    assert not results["max cost"].passed
