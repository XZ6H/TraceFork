# ADR 0004: One boundary abstraction shared by recording and replay

- **Status:** accepted
- **Date:** 2026-09-02

## Context

Nondeterministic effects in an agent (LLM calls, tools, HTTP, time,
randomness) must be interceptable for recording *and* replayable under
policy. Implementing interception twice — once to record, once to replay —
guarantees drift between the two modes and doubles the places where secrets,
async semantics and error behavior can go wrong. Framework-specific code must
also stay out of the core (PRD §24, plan Rule 1 and Rule 5).

## Decision

Every nondeterministic effect is routed through a single abstraction:

```python
response = boundary.invoke(request, call_live)
```

A *boundary runtime* decides how `invoke` behaves based on the execution
context mode:

```text
NORMAL   — pass through, no recording
RECORD   — call_live, then persist the invocation
REPLAY   — match the request against recordings, return the response
MOCK     — return a user-supplied response (never the real call)
FAULT    — inject a configured failure instead of calling through
```

Rules:

1. Adapters (OpenAI, httpx, Python tools, later frameworks) convert native
   calls into boundary requests and SDK responses out of boundary responses.
   They never implement mode logic themselves.
2. The mode comes from an `ExecutionContext` carried in `contextvars` — never
   from global mutable state — so concurrent traces and async task trees are
   isolated.
3. Fingerprinting, matching, occurrence tracking and fail-closed behavior are
   implemented once, in the runtime, and therefore hold for every adapter.

## Consequences

- One code path to verify for correctness, security and async behavior.
- Supporting a new framework is an adapter, not a fork of the engine.
- Adapters must model requests/responses as canonical data (not SDK objects)
  at the boundary; each adapter owns that translation.
