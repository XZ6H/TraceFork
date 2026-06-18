# Replay Semantics

What each execution mode means, how policies are resolved, and exactly what a
hermetic replay guarantees. These semantics are contractual: the CLI exit
codes (M10) and the CI story are built on them.

## Execution modes

The boundary runtime operates in one of three modes, carried in a
`contextvars`-based `ExecutionContext` — never in global state:

| Mode | Behavior |
|---|---|
| `NORMAL` | Pass through. Boundary calls execute live; nothing is recorded. |
| `RECORD` | Every boundary executes live exactly once; the interaction is recorded into the active trace. |
| `REPLAY` | Every boundary is routed by the replay policy. |

`record()` installs a RECORD context; a `ReplaySession` installs a REPLAY
context. Contexts propagate across `asyncio` task trees.

## Replay policy resolution

Each boundary call inside a replay is resolved to a replay mode:

```text
1. exact boundary rule      (per-tool overrides, e.g. tools.search_orders)
2. boundary-type rule       (llm: ..., http: ...)
3. default                  (default: replay)
```

The resolved mode is applied without exception:

| Mode | Behavior |
|---|---|
| `REPLAY` | Serve the recorded response. The live callable does not exist on this code path — it cannot run, by construction. |
| `LIVE` | Execute for real; the call is counted as a live boundary in the result. |
| `MOCK` / `FAULT` | Not implemented yet; the session raises `ReplayPolicyError` and the live call does not run. |

There is no configuration that turns an unmatched call into a live call.

## What hermetic replay guarantees

A hermetic replay (every boundary resolved to `REPLAY`) that reports `passed`
guarantees:

- **0 live boundary executions**,
- **0 network calls** on LLM/HTTP families,
- every replayed boundary corresponds to exactly one recorded invocation,
- the same fixture + the same code produce the same result (the replay is
  deterministic),
- the fixture bytes were not mutated.

The candidate trace marks replayed spans with `attributes["replay"] =
"replayed"` so nothing is ambiguous about what executed for real.

## Selective replay

Hybrid runs freeze part of the world and re-execute the rest:

- `llm: live, tools: replay` — isolates prompt/model changes from external
  drift.
- `llm: replay, tools: live` — tests changed tool implementations against
  historical LLM decisions.
- per-tool overrides (`tools.search_orders: live`) — replay everything else.

Live boundaries are reported explicitly on the result
(`live_boundaries`, `network_calls`); a hybrid run is never described as
hermetic (`is_hermetic` is false when anything ran live).

## Result report

Every replay produces a `ReplayResult`:

| Field | Meaning |
|---|---|
| `status` | `passed` or `failed`. Failed when the block raised or any call went unmatched. |
| `trace` | The candidate trace (spans + invocations of this execution). |
| `matched` | Count of boundary calls served from the recording. |
| `unexpected` | Mismatch diagnostics for calls that matched nothing. |
| `unused_recordings` | Recorded interactions the candidate never needed (warning-level signal). |
| `live_boundaries` | Which boundaries executed live, as `type.name`. |
| `network_calls` | Live calls on LLM/HTTP families. |
| `is_hermetic` | True when nothing ran live and nothing went unmatched. |

Unused recordings do not fail the replay by default; they mean the candidate
took a shorter or different path. Strict mode may escalate later.

## Wall-clock latency

Replayed runs are fast because nothing external executes; their latency says
nothing about production latency. Latency assertions are only meaningful for
live runs — hermetic replay never makes latency claims.
