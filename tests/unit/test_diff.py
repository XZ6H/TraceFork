"""TF-130..134: diff engine — LCS alignment, resource deltas, first divergence."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tracefork.diff import diff_traces
from tracefork.metrics import TableCostCalculator
from tracefork.models import BoundaryInvocation, Provenance, Span, SpanKind, Trace

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_trace(
    trajectory: list[tuple[str, str]],
    usage: dict[str, Any] | None = None,
    tool_calls: int = 0,
) -> Trace:
    spans = [
        Span(span_id=f"sp_{i}", kind=SpanKind(kind), name=name, started_at=T0)
        for i, (kind, name) in enumerate(trajectory)
    ]
    invocations = []
    if usage is not None:
        invocations.append(
            BoundaryInvocation(
                boundary_type="llm.openai",
                name="gpt-test",
                span_id="sp_0",
                metadata={"usage": usage},
            )
        )
    for i in range(tool_calls):
        invocations.append(
            BoundaryInvocation(boundary_type="tool.python", name=f"tool_{i}", span_id="sp_0")
        )
    return Trace(
        trace_id="tr_1",
        name="case",
        started_at=T0,
        completed_at=T0 + timedelta(seconds=3),
        provenance=Provenance(),
        spans=spans,
        invocations=invocations,
    )


class TestAlignment:
    def test_identical_trajectories_fully_match(self) -> None:
        baseline = make_trace([("tool", "search"), ("tool", "fetch")])
        candidate = make_trace([("tool", "search"), ("tool", "fetch")])
        result = diff_traces(baseline, candidate)
        assert all(op.op == "match" for op in result.ops)
        assert result.first_divergence is None

    def test_inserted_tool_is_reported_in_order(self) -> None:
        baseline = make_trace([("tool", "search"), ("tool", "fetch"), ("llm", "summarize")])
        candidate = make_trace(
            [("tool", "search"), ("tool", "search"), ("tool", "fetch"), ("llm", "summarize")]
        )
        result = diff_traces(baseline, candidate)
        assert [(op.op, op.candidate.name if op.candidate else None) for op in result.ops] == [
            ("match", "search"),
            ("insert", "search"),
            ("match", "fetch"),
            ("match", "summarize"),
        ]

    def test_removed_tool_is_reported(self) -> None:
        baseline = make_trace([("tool", "search"), ("tool", "verify"), ("tool", "fetch")])
        candidate = make_trace([("tool", "search"), ("tool", "fetch")])
        result = diff_traces(baseline, candidate)
        assert [(op.op, op.baseline.name if op.baseline else None) for op in result.ops] == [
            ("match", "search"),
            ("remove", "verify"),
            ("match", "fetch"),
        ]

    def test_kind_change_is_remove_plus_insert(self) -> None:
        baseline = make_trace([("tool", "check_policy"), ("tool", "deny_refund")])
        candidate = make_trace([("tool", "check_policy"), ("tool", "refund_order")])
        result = diff_traces(baseline, candidate)
        labels = [
            (op.op, op.baseline.name if op.baseline else op.candidate.name) for op in result.ops
        ]
        assert labels == [
            ("match", "check_policy"),
            ("remove", "deny_refund"),
            ("insert", "refund_order"),
        ]

    def test_first_divergence_points_at_first_difference(self) -> None:
        baseline = make_trace([("tool", "search"), ("tool", "fetch")])
        candidate = make_trace([("tool", "search"), ("tool", "search"), ("tool", "fetch")])
        result = diff_traces(baseline, candidate)
        assert result.first_divergence is not None
        assert result.first_divergence.position == 1
        assert result.first_divergence.reason == "inserted"
        assert result.first_divergence.candidate is not None
        assert result.first_divergence.candidate.name == "search"

    def test_kind_filter_limits_trajectory(self) -> None:
        baseline = make_trace([("llm", "plan"), ("tool", "search"), ("http", "GET /x")])
        candidate = make_trace([("llm", "plan"), ("tool", "search"), ("http", "GET /x")])
        result = diff_traces(baseline, candidate, kinds={SpanKind.TOOL})
        assert [op.op for op in result.ops] == ["match"]


class TestResources:
    def test_resource_deltas_with_percentages(self) -> None:
        baseline = make_trace(
            [("tool", "search")], usage={"input_tokens": 2841, "output_tokens": 0}, tool_calls=1
        )
        candidate = make_trace(
            [("tool", "search"), ("tool", "fetch")],
            usage={"input_tokens": 4293, "output_tokens": 0},
            tool_calls=2,
        )
        result = diff_traces(baseline, candidate)
        by_name = {delta.name: delta for delta in result.resources}
        assert by_name["tool_calls"].baseline == 1
        assert by_name["tool_calls"].candidate == 2
        assert by_name["tool_calls"].change_percent == pytest.approx(100.0)
        assert by_name["tokens"].baseline == 2841
        assert by_name["tokens"].candidate == 4293
        assert by_name["tokens"].change_percent == pytest.approx(51.11, abs=0.01)

    def test_cost_delta_uses_calculator(self) -> None:
        baseline = make_trace(
            [("tool", "s")], usage={"input_tokens": 1_000_000, "output_tokens": 0}
        )
        candidate = make_trace(
            [("tool", "s")], usage={"input_tokens": 2_000_000, "output_tokens": 0}
        )
        calculator = TableCostCalculator({"gpt-test": (1.0, 0.0)})
        result = diff_traces(baseline, candidate, cost_calculator=calculator)
        cost = next(delta for delta in result.resources if delta.name == "cost_usd")
        assert cost.baseline == pytest.approx(1.0)
        assert cost.candidate == pytest.approx(2.0)
        assert cost.change_percent == pytest.approx(100.0)

    def test_zero_baseline_yields_null_percent(self) -> None:
        baseline = make_trace([("tool", "s")])
        candidate = make_trace([("tool", "s")], usage={"input_tokens": 10, "output_tokens": 0})
        result = diff_traces(baseline, candidate)
        tokens = next(delta for delta in result.resources if delta.name == "tokens")
        assert tokens.baseline is None
        assert tokens.candidate == 10
        assert tokens.change_percent is None
