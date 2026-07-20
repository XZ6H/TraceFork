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
    """Deterministic edit-script alignment over typed nodes (TF-130).

    Myers' O(ND) shortest-edit-script algorithm (the algorithm behind
    ``diff``/``git diff``): runtime scales with the number of *differences*,
    so a 1,000-node diff with 200 edits costs milliseconds. Tie-breaking is
    deterministic: removals before insertions, matching the LCS order the
    earlier DP produced.
    """
    n, m = len(baseline), len(candidate)

    # Trim the common prefix and suffix; Myers then runs on the middle only.
    prefix = 0
    while prefix < n and prefix < m and _equal(baseline[prefix], candidate[prefix]):
        prefix += 1
    suffix_a, suffix_b = n, m
    while (
        suffix_a > prefix
        and suffix_b > prefix
        and _equal(baseline[suffix_a - 1], candidate[suffix_b - 1])
    ):
        suffix_a -= 1
        suffix_b -= 1

    middle_ops = _myers(
        baseline[prefix:suffix_a],
        candidate[prefix:suffix_b],
    )

    ops: list[DiffOp] = []
    for position in range(prefix):
        ops.append(DiffOp("match", baseline[position], candidate[position]))
    ops.extend(middle_ops)
    # The trimmed common suffix matches pairwise (offsets stay aligned).
    for offset in range(n - suffix_a):
        ops.append(DiffOp("match", baseline[suffix_a + offset], candidate[suffix_b + offset]))
    return ops


def _myers(a: list[TrajectoryNode], b: list[TrajectoryNode]) -> list[DiffOp]:
    """Myers O(ND) diff over the differing middle region."""
    n, m = len(a), len(b)
    if n == 0:
        return [DiffOp("insert", None, node) for node in b]
    if m == 0:
        return [DiffOp("remove", node, None) for node in a]

    max_d = n + m
    v: dict[int, int] = {1: 0}
    trace: list[dict[int, int]] = []
    for d in range(max_d + 1):
        trace.append(v.copy())
        for k in range(-d, d + 1, 2):
            # k == -d forces a removal-first tie-break (deterministic).
            if k == -d or (k != d and v[k - 1] < v[k + 1]):
                x = v[k + 1]  # insertion from candidate
            else:
                x = v[k - 1] + 1  # removal from baseline
            y = x - k
            while x < n and y < m and _equal(a[x], b[y]):
                x += 1
                y += 1
            v[k] = x
            if x >= n and y >= m:
                return _myers_backtrack(trace, a, b)

    raise AssertionError("unreachable: Myers edit distance is bounded by n + m")


def _myers_backtrack(
    trace: list[dict[int, int]], a: list[TrajectoryNode], b: list[TrajectoryNode]
) -> list[DiffOp]:
    ops: list[DiffOp] = []
    x, y = len(a), len(b)
    for d in range(len(trace) - 1, 0, -1):
        v = trace[d]
        k = x - y
        # Same tie-break as the forward pass: removals before insertions.
        if k == -d or (k != d and v[k - 1] < v[k + 1]):
            prev_k = k + 1
        else:
            prev_k = k - 1
        prev_x = v[prev_k]
        prev_y = prev_x - prev_k
        while x > prev_x and y > prev_y:
            ops.append(DiffOp("match", a[x - 1], b[y - 1]))
            x -= 1
            y -= 1
        if x == prev_x:
            ops.append(DiffOp("insert", None, b[y - 1]))
        else:
            ops.append(DiffOp("remove", a[x - 1], None))
        x, y = prev_x, prev_y
    while x > 0 and y > 0:
        ops.append(DiffOp("match", a[x - 1], b[y - 1]))
        x -= 1
        y -= 1
    ops.reverse()
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
