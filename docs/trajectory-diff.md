# Trajectory Diff

How baseline and candidate executions are compared (TF-130..134).

## Alignment

Both traces are flattened into **logical trajectories** — the ordered list of
boundary-kind spans (`llm`, `tool`, `http` by default; filterable). Nodes are
compared by typed equality: kind **and** name.

Alignment uses a deterministic **longest-common-subsequence** (LCS) over the
two node sequences:

- equal nodes align as `match`,
- candidate-only nodes are `insert` (`+`),
- baseline-only nodes are `remove` (`-`),
- ties break deterministically (removal first), so the same inputs always
  produce the same report.

Full graph-edit-distance is deliberately out of scope for v0.1; exact
trajectory equality is *not* the default — a candidate that reaches the same
outcome through extra benign steps diffs cleanly instead of failing.

## First divergence

The first non-match in the alignment is reported with its position, the
offending node and the reason (`inserted` or `removed`). This is the earliest
behavioral difference between the two executions.

## Resource deltas

Resource metrics (tokens, estimated cost, LLM/tool call counts, wall-clock
duration) are extracted from both traces and reported as
`baseline -> candidate (+x%)`. Percentages are `None` when the baseline value
is zero or unknown — never a misleading infinity. Cost uses the configurable
`CostCalculator` (unknown model → no cost data).

## CLI

```console
$ tracefork diff baseline.json candidate.json
TRACE REGRESSION REPORT
baseline:  incident-1821
candidate: fixed-run

Trajectory
  llm:planner
  tool:get_order
  tool:get_customer
- tool:refund_order
+ tool:check_refund_policy

First divergence: removed at alignment step 3

Resources
  tool_calls: 3 -> 3 (+0.0%)
```

The `diff` command is informational; pass/fail verdicts belong to
the evaluation suites (see the CLI `eval` command and
`docs/replay-semantics.md`), which combine diffs' underlying metrics with
declarative expectations and regression thresholds.

## Semantic comparison modes

The PRD's strict/ordered/unordered modes map onto the assertion layer:
`require`/`forbid`/`before`/`max_calls` (TF-140..145) express
ordering and count constraints without demanding trajectory equality. The
diff engine itself always reports the structural difference.
