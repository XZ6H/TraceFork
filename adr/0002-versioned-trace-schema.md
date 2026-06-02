# ADR 0002: Versioned trace and fixture schemas from day one

- **Status:** accepted
- **Date:** 2026-09-02

## Context

Fixtures are recorded real executions committed to the user's repository and
expected to stay usable for months — that is their entire value (PRD §28:
"Do not assume you can safely change fixture format later"). Any schema change
that silently invalidates committed fixtures breaks users' regression suites
in ways they cannot diagnose.

## Decision

Every serialized trace carries a `schema_version` string, and every fixture is
wrapped in an envelope carrying a `fixture_version`, a creation timestamp, the
trace payload and an integrity block:

```json
{
  "fixture_version": "1",
  "created_at": "...",
  "trace": { "schema_version": "1.0", "...": "..." },
  "integrity": { "algorithm": "sha256", "digest": "..." }
}
```

Rules:

1. The reader validates the envelope version first, then the trace schema
   version. Unknown or unsupported versions raise `FixtureVersionError` —
   never a best-effort parse.
2. A migrations module in `serialization/` upgrades old supported versions to
   the current one; migrations are explicit, tested and documented.
3. Within a version, the schema is append-only: new fields are optional with
   safe defaults, existing fields never change meaning.
4. The integrity digest is computed over the canonical serialization of the
   envelope payload (UTF-8, sorted keys, stable separators, normalized
   datetimes). Corrupted fixtures fail validation on load, not at replay time.

## Consequences

- Fixture files are stable, diffable and Git-friendly.
- Schema evolution is a deliberate, reviewable act rather than an accident.
- Upfront version plumbing is cheap now and expensive to retrofit.
