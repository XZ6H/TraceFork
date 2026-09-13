# Trace and Fixture Schema

Version 1.0 of the trace schema, and version 1 of the fixture envelope (see
[ADR 0002](adr/0002-versioned-trace-schema.md)). Both are validated
strictly on load: unknown versions are rejected, unknown fields are rejected,
and the integrity digest must match.

## Trace

```json
{
  "schema_version": "1.0",
  "trace_id": "tr_...",
  "name": "customer-refund",
  "started_at": "2026-09-02T15:00:00Z",
  "completed_at": "2026-09-02T15:00:03Z",
  "input": "Refund order #31991",
  "output": {"status": "rejected"},
  "spans": [ {"...": "..."} ],
  "invocations": [ {"...": "..."} ],
  "metadata": {},
  "provenance": {"...": "..."},
  "status": "completed"
}
```

- `schema_version`: `"1.0"`. Within a version the schema is append-only:
  new fields are optional, existing fields never change meaning.
- `status`: `running` | `completed` | `failed`.
- timestamps are UTC ISO-8601; naive datetimes are normalized to UTC.

## Span

```json
{
  "span_id": "sp_...",
  "parent_span_id": "sp_...",
  "kind": "llm",
  "name": "planner",
  "input": {},
  "output": {},
  "started_at": "2026-09-02T15:00:00Z",
  "completed_at": "2026-09-02T15:00:01Z",
  "attributes": {},
  "status": "ok",
  "error": {"exception_type": "ValueError", "message": "..."}
}
```

- `kind`: `agent` | `llm` | `tool` | `http` | `retriever` | `workflow` | `custom`.
- `status`: `running` | `ok` | `error`.
- `error` is present only for failed spans and never contains stack traces
  (they leak local paths).
- `parent_span_id: null` marks a root span. Sibling order in `spans` is start
  order, never completion order.

## Boundary invocation

```json
{
  "boundary_type": "tool.echo",
  "name": "search_orders",
  "request": {"customer_id": 912},
  "response": {"orders": [1]},
  "fingerprint": "sha256-hex-64",
  "span_id": "sp_...",
  "parent_span_id": "sp_...",
  "parent_name": "agent",
  "occurrence": 0,
  "metadata": {}
}
```

The `fingerprint` covers boundary type, name and canonical request (see
[matching.md](matching.md)) — never timestamps or span IDs. `parent_name`
gives matching a stable logical-parent anchor; raw span IDs are not stable
across executions. Failed invocations store `response: null` and an `error`
entry in `metadata`.

## Fixture envelope

```json
{
  "fixture_version": "1",
  "created_at": "2026-09-02T15:00:03Z",
  "trace": {"...": "trace above"},
  "integrity": {"algorithm": "sha256", "digest": "hex-64"}
}
```

The digest is computed over the canonical serialization of the payload
(`fixture_version`, `created_at`, `trace`) — never over the integrity block
itself. Loading a fixture whose digest does not match fails with
`FixtureCorruptError` before anything is replayed.

## Canonical serialization

Canonical JSON is the determinism basis for digests and fingerprints:

- UTF-8, sorted keys, compact separators (``,`:`),
- datetimes normalized to UTC with `Z` suffix,
- enums serialized as values, UUIDs as strings,
- anything else (arbitrary objects, NaN/Infinity) is rejected with
  `TypeError`, never stringified.

## Storage

`FilesystemFixtureStore` persists one envelope per file as
`<root>/<name>.json`, written atomically (temp file + rename). Names must
match `^[A-Za-z0-9][A-Za-z0-9._-]*$` — path traversal is rejected.
