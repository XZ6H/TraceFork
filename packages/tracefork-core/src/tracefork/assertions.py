"""Trace assertions (TF-140..145).

Assertions return evidence, not just booleans. Tool expectations apply to
tool-family boundary invocations. Resource maximums use the metrics
extractor; an unknown metric (no usage data) passes with explicit evidence
rather than silently comparing against zero.

Wall-clock latency is deliberately not a deterministic assertion: hermetic
replay latency says nothing about production latency (plan TF-145).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tracefork.metrics import CostCalculator, TraceMetrics, extract_metrics


class ToolExpectations(BaseModel):
    """Required and forbidden tool names."""

    model_config = ConfigDict(extra="forbid")

    require: list[str] = Field(default_factory=list)
    forbid: list[str] = Field(default_factory=list)


class ResourceMaximums(BaseModel):
    """Upper bounds on resource consumption."""

    model_config = ConfigDict(extra="forbid")

    tool_calls: int | None = None
    llm_calls: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None


class TraceExpectations(BaseModel):
    """Declarative expectations evaluated against a candidate trace."""

    model_config = ConfigDict(extra="forbid")

    tools: ToolExpectations = Field(default_factory=ToolExpectations)
    before: list[tuple[str, str]] = Field(default_factory=list)
    max_calls: dict[str, int] = Field(default_factory=dict)
    max: ResourceMaximums = Field(default_factory=ResourceMaximums)


@dataclass(frozen=True)
class AssertionResult:
    """One assertion outcome with evidence."""

    name: str
    passed: bool
    detail: str


def evaluate_expectations(
    trace: Any,
    expectations: TraceExpectations,
    *,
    cost_calculator: CostCalculator | None = None,
) -> list[AssertionResult]:
    """Evaluate every expectation against *trace*, returning evidence."""
    tool_positions = _tool_positions(trace)
    metrics = extract_metrics(trace, cost_calculator=cost_calculator)

    results: list[AssertionResult] = []
    results.extend(_require_tools(tool_positions, expectations.tools.require))
    results.extend(_forbid_tools(tool_positions, expectations.tools.forbid))
    results.extend(_ordering(tool_positions, expectations.before))
    results.extend(_max_calls(tool_positions, expectations.max_calls))
    results.extend(_resource_maximums(metrics, expectations.max))
    return results


def _tool_positions(trace: Any) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = {}
    position = 0
    for invocation in trace.invocations:
        if invocation.boundary_type.split(".", 1)[0] == "tool":
            positions.setdefault(invocation.name, []).append(position)
            position += 1
    return positions


def _require_tools(positions: dict[str, list[int]], required: list[str]) -> list[AssertionResult]:
    results = []
    for name in required:
        calls = positions.get(name, [])
        results.append(
            AssertionResult(
                name=f"required tool {name}",
                passed=bool(calls),
                detail=f"called {len(calls)} time(s)",
            )
        )
    return results


def _forbid_tools(positions: dict[str, list[int]], forbidden: list[str]) -> list[AssertionResult]:
    results = []
    for name in forbidden:
        calls = positions.get(name, [])
        results.append(
            AssertionResult(
                name=f"forbidden tool {name}",
                passed=not calls,
                detail=f"called {len(calls)} time(s)" if calls else "not called",
            )
        )
    return results


def _ordering(
    positions: dict[str, list[int]], pairs: list[tuple[str, str]]
) -> list[AssertionResult]:
    results = []
    for first, second in pairs:
        first_calls = positions.get(first, [])
        second_calls = positions.get(second, [])
        # Exists semantics: some occurrence of `first` precedes some
        # occurrence of `second` (first(first) < last(second)).
        passed = bool(first_calls) and bool(second_calls) and first_calls[0] < second_calls[-1]
        detail = (
            f"{first} #{first_calls[0] + 1} before {second} #{second_calls[-1] + 1}"
            if passed
            else f"{first} does not precede {second}"
        )
        results.append(
            AssertionResult(name=f"ordering {first} before {second}", passed=passed, detail=detail)
        )
    return results


def _max_calls(positions: dict[str, list[int]], max_calls: dict[str, int]) -> list[AssertionResult]:
    results = []
    for name, limit in max_calls.items():
        calls = len(positions.get(name, []))
        results.append(
            AssertionResult(
                name=f"max calls for {name}",
                passed=calls <= limit,
                detail=f"{calls} call(s), limit {limit}",
            )
        )
    return results


def _resource_maximums(metrics: TraceMetrics, maximums: ResourceMaximums) -> list[AssertionResult]:
    results = []
    if maximums.tool_calls is not None:
        results.append(
            AssertionResult(
                name="max tool calls",
                passed=metrics.tool_calls <= maximums.tool_calls,
                detail=f"{metrics.tool_calls} call(s), limit {maximums.tool_calls}",
            )
        )
    if maximums.llm_calls is not None:
        results.append(
            AssertionResult(
                name="max llm calls",
                passed=metrics.llm_calls <= maximums.llm_calls,
                detail=f"{metrics.llm_calls} call(s), limit {maximums.llm_calls}",
            )
        )
    if maximums.total_tokens is not None:
        if metrics.total_tokens is None:
            results.append(
                AssertionResult(
                    name="max total tokens",
                    passed=True,
                    detail="no token data recorded",
                )
            )
        else:
            results.append(
                AssertionResult(
                    name="max total tokens",
                    passed=metrics.total_tokens <= maximums.total_tokens,
                    detail=f"{metrics.total_tokens} tokens, limit {maximums.total_tokens}",
                )
            )
    if maximums.cost_usd is not None:
        if metrics.estimated_cost_usd is None:
            results.append(
                AssertionResult(
                    name="max cost",
                    passed=True,
                    detail="no cost data (unknown model or no calculator)",
                )
            )
        else:
            results.append(
                AssertionResult(
                    name="max cost",
                    passed=metrics.estimated_cost_usd <= maximums.cost_usd,
                    detail=f"${metrics.estimated_cost_usd:.4f}, limit ${maximums.cost_usd:.4f}",
                )
            )
    return results
