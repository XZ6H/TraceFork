# ADR 0003: Fail-closed replay

- **Status:** accepted
- **Date:** 2026-09-02

## Context

A replay library that quietly falls back to the real dependency when a
recorded interaction cannot be matched is worse than no library at all. It
would silently call production APIs from tests, leak side effects, and make
passing tests meaningless (PRD §13, §38). Trust in TraceFork depends entirely
on this never happening.

## Decision

Replay fails closed:

1. When an incoming boundary call cannot be matched to a recorded interaction,
   the runtime raises `ReplayMismatchError` with diagnostics (the received
   request, the closest recorded candidates, and a similarity score). It never
   performs the real call.
2. Hermetic replay passes only if the number of live boundaries is zero *and*
   the number of unexpected boundaries is zero. Both counters are part of the
   replay result and the CLI output.
3. Recordings that went unused during replay are reported as warnings by
   default; strict mode may escalate this later.
4. There is no configuration, flag or fallback path that turns an unmatched
   call into a live call. Adapters cannot opt out of this behavior — it lives
   in the runtime, below the adapter layer (see [ADR 0004](0004-boundary-abstraction.md)).

## Consequences

- A green hermetic replay is a meaningful statement: zero live calls, zero
  external side effects.
- Debugging UX is critical: mismatch diagnostics must show what was received,
  what was recorded, and the delta. We invest in this.
- Users must record complete executions; partial fixtures fail loudly instead
  of quietly passing.
