"""Trajectory diff engine (TF-130..134).

Baseline and candidate logical trajectories are aligned with deterministic
longest-common-subsequence matching (typed node equality: kind + name). Full
graph-edit-distance is explicitly out of scope for v0.1. Resource deltas come
from the metrics extractor; the first divergence is the earliest non-match in
the alignment.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracefork.metrics import CostCalculator, TraceMetrics, extract_metrics
from tracefork.models import SpanKind, Trace
from tracefork.trajectory import TrajectoryNode, logical_trajectory


@dataclass(frozen=True)
class DiffOp:
    """One step of the alignment: a match, an insertion, or a removal."""

    op: str  # "match" | "insert" | "remove"
    baseline: TrajectoryNode | None
    candidate: TrajectoryNode | None


@dataclass(frozen=True)
class ResourceDelta:
    """Change in one resource metric between baseline and candidate."""

    name: str
    baseline: float | int | None
    candidate: float | int | None
    change_percent: float | None


@dataclass(frozen=True)
class FirstDivergence:
    """The earliest point where candidate behavior differs from baseline."""

    position: int
    reason: str  # "inserted" | "removed"
    baseline: TrajectoryNode | None
    candidate: TrajectoryNode | None


@dataclass(frozen=True)
class DiffResult:
    ops: list[DiffOp]
    resources: list[ResourceDelta]
    first_divergence: FirstDivergence | None
    baseline_trajectory: list[TrajectoryNode]
    candidate_trajectory: list[TrajectoryNode]
    baseline_metrics: TraceMetrics
    candidate_metrics: TraceMetrics


def diff_traces(
    baseline: Trace,
    candidate: Trace,
    *,
    cost_calculator: CostCalculator | None = None,
    kinds: frozenset[SpanKind] | set[SpanKind] | None = None,
) -> DiffResult:
    """Diff two traces: trajectory alignment plus resource deltas (TF-130)."""
    baseline_nodes = logical_trajectory(baseline, kinds=kinds)
    candidate_nodes = logical_trajectory(candidate, kinds=kinds)
    ops = _align(baseline_nodes, candidate_nodes)
    divergence = _first_divergence(ops)
    baseline_metrics = extract_metrics(baseline, cost_calculator=cost_calculator)
    candidate_metrics = extract_metrics(candidate, cost_calculator=cost_calculator)
    return DiffResult(
        ops=ops,
        resources=_resource_deltas(baseline_metrics, candidate_metrics),
        first_divergence=divergence,
        baseline_trajectory=baseline_nodes,
        candidate_trajectory=candidate_nodes,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
    )


def _align(baseline: list[TrajectoryNode], candidate: list[TrajectoryNode]) -> list[DiffOp]:
    """Deterministic LCS alignment over typed nodes (TF-130)."""
    n, m = len(baseline), len(candidate)
    # lcs[i][j] = LCS length of baseline[i:] and candidate[j:]
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if _equal(baseline[i], candidate[j]):
                lcs[i][j] = 1 + lcs[i + 1][j + 1]
            else:
                lcs[i][j] = max(lcs[i + 1][j], lcs[i][j + 1])

    ops: list[DiffOp] = []
    i = j = 0
    while i < n and j < m:
        if _equal(baseline[i], candidate[j]):
            ops.append(DiffOp("match", baseline[i], candidate[j]))
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            ops.append(DiffOp("remove", baseline[i], None))
            i += 1
        else:
            ops.append(DiffOp("insert", None, candidate[j]))
            j += 1
    while i < n:
        ops.append(DiffOp("remove", baseline[i], None))
        i += 1
    while j < m:
        ops.append(DiffOp("insert", None, candidate[j]))
        j += 1
    return ops


def _equal(a: TrajectoryNode, b: TrajectoryNode) -> bool:
    return a.kind is b.kind and a.name == b.name


def _first_divergence(ops: list[DiffOp]) -> FirstDivergence | None:
    for position, op in enumerate(ops):
        if op.op == "remove":
            return FirstDivergence(position, "removed", op.baseline, None)
        if op.op == "insert":
            return FirstDivergence(position, "inserted", None, op.candidate)
    return None


def _resource_deltas(baseline: TraceMetrics, candidate: TraceMetrics) -> list[ResourceDelta]:
    pairs: list[tuple[str, float | int | None, float | int | None]] = [
        ("tokens", baseline.total_tokens, candidate.total_tokens),
        ("cost_usd", baseline.estimated_cost_usd, candidate.estimated_cost_usd),
        ("llm_calls", baseline.llm_calls, candidate.llm_calls),
        ("tool_calls", baseline.tool_calls, candidate.tool_calls),
        ("duration_seconds", baseline.wall_clock_seconds, candidate.wall_clock_seconds),
    ]
    return [
        ResourceDelta(name=name, baseline=b, candidate=c, change_percent=_percent(b, c))
        for name, b, c in pairs
    ]


def _percent(baseline: float | int | None, candidate: float | int | None) -> float | None:
    if baseline is None or candidate is None or baseline == 0:
        return None
    return (candidate - baseline) / baseline * 100
