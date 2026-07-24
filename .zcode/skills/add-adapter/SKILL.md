---
name: add-adapter
description: Checklist for adding a new integration adapter to TraceFork (LLM provider, HTTP client, tool system, or agent framework). Use when the user asks to support a new framework, provider, SDK, or client library.
---

# Adding an adapter to TraceFork

Read [docs/adapters.md](../../../docs/adapters.md) and
[ADR 0004](../../../adr/0004-boundary-abstraction.md) first. The rule:
**adapters translate; the core decides.**

## Checklist

1. **Boundary type**: `family.name` (e.g. `llm.anthropic`, `http.rest`).
   The family decides the recorded span kind and the replay-policy family.
2. **Instrumentation point**: capture the native call into a canonical
   JSON-safe request payload and call
   `runtime.invoke(boundary_type, name, request, call_live)` — with
   `call_live` performing the real native call. Name should be stable and
   meaningful (model name, URL path, function name).
3. **Handler** (implements `BoundaryHandler`):
   - `execute(request, call_live)` — run the live call, return the canonical
     response payload plus metadata (usage, latency, finish reason).
   - `restore(response, metadata)` — rebuild the native object application
     code expects. Raw dicts are not acceptable if users normally get SDK
     objects.
4. **Register** the handler: `runtime.registry.register(boundary_type, handler)`.
5. **Tests** (TDD, all required):
   - record: SDK/native objects preserved, invocation fields correct,
     span kind correct,
   - hermetic replay with a dead transport (network attempt fails the test),
   - mismatch fails closed with diagnostics,
   - secret hygiene: credentials never appear in fixture bytes,
   - sync and async paths if both exist; streaming if applicable.
6. **Docs**: add a row to docs/adapters.md roadmap table and the CHANGELOG.

## Prohibited in adapters

Mode logic, matching, fallback-to-live, global registries, secrets in
payloads, dependencies on the core's internals beyond the public contract.
