# Core Concepts

TraceFork records agent executions as traces, freezes them as fixtures, and
replays them under an explicit policy. This document defines the vocabulary
used everywhere else.

## Trace

One complete execution of an agent. A trace carries its input, output,
status, provenance (git state), a tree of spans, and the boundary invocations
recorded during the run.

## Span

One execution unit inside a trace: an agent step, an LLM call, a tool call,
an HTTP request, a retriever, a workflow, or a custom unit. Spans link to
their parent via `parent_span_id`, forming the execution tree from which the
logical graph is derived (see [ADR 0005](../adr/0005-execution-graph.md)).
Sibling order is start order, never completion order.

## Boundary

A nondeterministic edge between the agent and the outside world: an LLM call,
a tool invocation, an HTTP request. All boundary traffic is routed through a
single runtime which decides — per call — whether it executes live or is
served from a recording (see [ADR 0004](../adr/0004-boundary-abstraction.md)).

## Boundary invocation

One recorded boundary interaction: the canonical request, the response, a
fingerprint of type + name + canonical request, the logical parent, and an
occurrence index. Invocations are the unit of replay matching; spans are the
human view, invocations are the machine view.

## Fixture

An immutable, versioned, integrity-checked snapshot of a trace, wrapped in an
envelope and stored as a single JSON file. Fixtures are Git-friendly and
serve as regression baselines. See [trace-schema.md](trace-schema.md).

## Recording vs. replay

- **Recording** runs the agent for real and captures every boundary
  interaction into a trace.
- **Replay** runs the *current* agent code while the boundary layer serves
  recorded responses according to a policy. The execution produces a candidate
  trace plus a result report (matched, unexpected, unused, live).

## Execution modes

The boundary runtime operates in one of three modes:

| Mode | Behavior |
|---|---|
| `NORMAL` | Pass through; no recording, no interception. |
| `RECORD` | Execute live exactly once per boundary call; record the interaction. |
| `REPLAY` | Route each boundary by the replay policy (below). |

## Replay modes (per boundary)

| Mode | Behavior |
|---|---|
| `REPLAY` | Serve the recorded response; the live call never exists on this path. |
| `LIVE` | Execute for real; counted as a live boundary in the result. |
| `MOCK` | User-supplied response. Not implemented yet (fails closed). |
| `FAULT` | Injected failure. Not implemented yet (fails closed). |

Modes are always explicit — there is no implicit fallback to live (see
[ADR 0003](../adr/0003-fail-closed-replay.md)).

## Hermetic, hybrid, live

- **Hermetic replay**: every boundary replayed. Deterministic, offline,
  zero model cost. Reports zero live boundaries and zero network calls.
- **Hybrid replay**: some boundaries live (e.g. `llm: live`, tools replayed).
  Controlled but nondeterministic.
- **Live evaluation**: everything executes normally.

TraceFork never claims that identical prompts guarantee identical model
output; hermetic determinism comes from replaying, not from the model.

## Baseline and candidate

A **baseline** is a known-good execution (typically the fixture). A
**candidate** is the execution produced by changed code, prompt, model, tool
or configuration. Comparing them is the purpose of the diff engine
(trajectory-diff.md, forthcoming at M13).
