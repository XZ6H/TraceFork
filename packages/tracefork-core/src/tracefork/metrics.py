"""Trace metrics extraction and the cost model abstraction (TF-110, TF-111).

Provider prices are never hard-coded into the core: cost estimation goes
through a :class:`CostCalculator`. Unknown models yield ``None`` — never a
misleading zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tracefork.models import SpanKind, Trace


class CostCalculator(Protocol):
    """Estimates the USD cost of one LLM call; unknown models return ``None``."""

    def cost_usd(
        self, model: str, input_tokens: int | None, output_tokens: int | None
    ) -> float | None: ...


@dataclass(frozen=True)
class TableCostCalculator:
    """Static per-1M-token price table."""

    prices: dict[str, tuple[float, float]]

    def cost_usd(
        self, model: str, input_tokens: int | None, output_tokens: int | None
    ) -> float | None:
        table = self.prices.get(model)
        if table is None or input_tokens is None or output_tokens is None:
            return None
        input_usd_per_1m, output_usd_per_1m = table
        return (
            input_tokens / 1_000_000 * input_usd_per_1m
            + output_tokens / 1_000_000 * output_usd_per_1m
        )


@dataclass
class TraceMetrics:
    """Aggregated resource metrics for one trace."""

    llm_calls: int = 0
    tool_calls: int = 0
    http_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    wall_clock_seconds: float | None = None
    llm_latency_ms: int | None = None
    tool_latency_ms: int | None = None


def extract_metrics(trace: Trace, *, cost_calculator: CostCalculator | None = None) -> TraceMetrics:
    """Extract metrics from a trace's spans and boundary invocations."""
    llm_calls = tool_calls = http_calls = 0
    input_tokens = output_tokens = 0
    has_tokens = False
    cost = 0.0
    has_cost = False

    for invocation in trace.invocations:
        family = invocation.boundary_type.split(".", 1)[0]
        if family == "llm":
            llm_calls += 1
            usage = invocation.metadata.get("usage") or {}
            if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
                has_tokens = True
                input_tokens += usage.get("input_tokens") or 0
                output_tokens += usage.get("output_tokens") or 0
            if cost_calculator is not None:
                call_cost = cost_calculator.cost_usd(
                    invocation.name,
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                )
                if call_cost is not None:
                    cost += call_cost
                    has_cost = True
        elif family == "tool":
            tool_calls += 1
        elif family == "http":
            http_calls += 1

    return TraceMetrics(
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        http_calls=http_calls,
        input_tokens=input_tokens if has_tokens else None,
        output_tokens=output_tokens if has_tokens else None,
        total_tokens=(input_tokens + output_tokens) if has_tokens else None,
        estimated_cost_usd=cost if has_cost else None,
        wall_clock_seconds=_wall_clock_seconds(trace),
        llm_latency_ms=_sum_latency(trace, SpanKind.LLM),
        tool_latency_ms=_sum_latency(trace, SpanKind.TOOL),
    )


def _wall_clock_seconds(trace: Trace) -> float | None:
    if trace.completed_at is None:
        return None
    return (trace.completed_at - trace.started_at).total_seconds()


def _sum_latency(trace: Trace, kind: SpanKind) -> int | None:
    total_ms = 0
    found = False
    for span in trace.spans:
        if span.kind is kind and span.completed_at is not None:
            found = True
            total_ms += int((span.completed_at - span.started_at).total_seconds() * 1000)
    return total_ms if found else None
