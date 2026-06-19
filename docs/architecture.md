# Architecture

TraceFork separates *what executed* (traces, fixtures) from *how executions
are controlled* (the boundary runtime). The layers, top to bottom:

```text
        ┌───────────────────────────┐
        │        CLI / UI           │   (M10 / later)
        └────────────┬──────────────┘
                     │
        ┌────────────▼──────────────┐
        │    Evaluation / Diff      │   (M13–M15)
        └────────────┬──────────────┘
                     │
        ┌────────────▼──────────────┐
        │       Replay Engine       │   ReplaySession, ReplayMatcher
        └────────────┬──────────────┘
                     │
        ┌────────────▼──────────────┐
        │      Boundary Runtime     │   modes, registry, invocation recording
        └──────┬───────────┬────────┘
               │           │
        Python tools   OpenAI / httpx / ...   (adapters translate only)
               └───────────┘
                     │
              Application / Agent
```

## Package layout

- `tracefork-core` — everything below the CLI: models, recording, boundaries,
  canonicalization, replay, storage, serialization. Zero framework
  dependencies (only Pydantic).
- Adapter packages (`tracefork-openai`, `tracefork-httpx`, ...) depend on
  core; core never depends on an adapter.

## Data flow

```text
Recording → Trace → Fixture → Replay → Candidate Trace
                                     ├── Diff        (M13)
                                     └── Evaluation  (M14/M15)
```

- **Recording**: `record()` installs a RECORD execution context; every
  boundary call and every span lands in a `Trace`.
- **Fixture**: the trace is wrapped in a versioned, digest-sealed envelope
  and stored as one JSON file.
- **Replay**: `ReplaySession` installs a REPLAY context over a fresh
  candidate `Recording`; the current application code runs while the policy
  decides each boundary's fate. Matching is exact and fail-closed.

## State and concurrency

- Execution state (recording, current span, execution context) lives in
  `contextvars`, never in global mutable state. Concurrent traces are
  isolated, and `asyncio` task trees inherit their enclosing context, so
  `asyncio.gather` children share the correct parent span with start-order
  sibling ordering (ADR 0005).
- Recording and replay share one boundary abstraction; there is exactly one
  interception path to verify for correctness, security and async behavior.

## Layering rules

1. The core knows nothing about agent frameworks.
2. Adapters never implement mode logic — they translate (see
   [adapters.md](adapters.md)).
3. Replay semantics (matching, fail-closed, policy resolution) live in the
   runtime, below the adapter layer, so they hold for every integration.
4. Storage backends sit behind the `FixtureStore` protocol; the filesystem
   backend is the v0.1 implementation.

## Deliberate exclusions (v0.1)

No hosted service, no accounts, no Postgres/Redis/Kafka, no distributed
workers, no dashboards. Those are confetti until the core is excellent.
