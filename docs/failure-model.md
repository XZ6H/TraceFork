# Failure Model

Every error TraceFork raises derives from `TraceForkError`. Underlying
library exceptions are never exposed as the public API.

## Hierarchy

```text
TraceForkError
├── RecordingError            recording could not proceed
├── FixtureError
│   ├── FixtureNotFoundError  referenced fixture does not exist
│   ├── FixtureCorruptError   malformed JSON, schema violation, digest mismatch
│   └── FixtureVersionError   unsupported fixture_version / schema_version
├── ReplayError
│   ├── ReplayMismatchError   no recorded interaction matched (fail closed)
│   ├── ReplayPolicyError     invalid policy or unsupported mode (MOCK/FAULT)
│   └── UnexpectedLiveCallError  (reserved; live calls are prevented upstream)
├── AdapterError
│   └── UnknownBoundaryError  no handler registered for the boundary type
└── EvaluationError           (assertions/eval suites, M14/M15)
```

## Fail-closed rules

1. **Replay never falls back to live.** An unmatched call raises
   `ReplayMismatchError`. The live callable is not on the code path of a
   replayed boundary — no configuration can change that.
2. **A replayed boundary cannot execute.** `ReplaySession.replay_invoke`
   does not even accept a live callable.
3. **Unsupported modes fail loudly.** MOCK and FAULT raise
   `ReplayPolicyError` until implemented.
4. **Corruption is caught at load.** Fixtures are digest-checked and
   schema-validated when loaded, never partially trusted at replay time.
5. **Canonicalization failures happen before side effects.** A request that
   cannot be canonicalized fails before any span, invocation or live call.

## Error causes and remedies

| Error | Typical cause | Remedy |
|---|---|---|
| `ReplayMismatchError` | Code/prompt changed the request sent to a replayed boundary | If intended: re-record the fixture. If not: fix the regression. Diagnostics show the delta. |
| `FixtureVersionError` | Fixture written by a different TraceFork version | Upgrade/downgrade, or migrate via `serialization.migrations` (future). |
| `FixtureCorruptError` | Hand-edited or truncated fixture file | Re-record; never hand-edit fixtures. |
| `UnknownBoundaryError` | Adapter not registered before replay | Import/register the adapter package. |
| `ReplayPolicyError` | MOCK/FAULT requested before they exist | Use LIVE or REPLAY for now. |

## What failure means in CI

`ReplayMismatchError` in a suite means: *the candidate's behavior diverged
from the recorded world*. That is a regression signal, not a flake — the
same fixture and the same code always produce the same verdict.
