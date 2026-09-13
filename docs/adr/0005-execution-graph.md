# ADR 0005: Execution graph derived from span parentage

- **Status:** accepted
- **Date:** 2026-09-02

## Context

Agents do not execute linearly: parallel tool calls, nested agents, handoffs,
retries and loops all occur. Diffing and divergence detection need
structure, but choosing a full graph database or an exotic serialization makes
fixtures brittle and unreadable.

## Decision

A trace stores spans as an ordered list, each carrying a `parent_span_id`.
The logical execution graph is *derived*, never stored separately:

```python
ExecutionGraph(nodes=..., edges=...)
```

- Edges are derived from parent relationships.
- Sibling order within a parent is the span start order, never completion
  order — parallel `asyncio.gather` calls must not depend on which task
  finished first.
- Loading a trace validates the graph: missing parents, cycles, duplicate
  span IDs and orphans are `FixtureError`s.
- The recorder constructs spans as a tree by construction, so cycles cannot be
  recorded; validation is the safety net for imported traces (OTel, custom
  JSON, later milestones).

Diff operates on this derived graph. Exact graph-edit-distance is explicitly
out of scope for v0.1; sequence alignment over logical trajectories comes
first (plan TF-130).

## Consequences

- Fixtures stay plain JSON lists — readable, diffable, Git-friendly.
- Graph structure is single-sourced from spans, so recording bugs surface as
  validation errors rather than silent corruption.
- Imported traces that cannot form a valid tree are rejected at load time
  with precise diagnostics.
