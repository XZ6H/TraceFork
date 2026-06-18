# Matching Semantics

How incoming boundary calls are matched to recorded interactions, and what
happens when they cannot be. Matching must be precise: a replay engine with
sloppy matching becomes spaghetti, and a replay library that falls back to
live calls is worse than no library at all.

## Canonical requests

Before fingerprinting, a request is canonicalized:

1. Ignore rules are applied (below) to remove volatile fields.
2. The payload is converted to canonical JSON form: sorted keys, compact
   separators, normalized datetimes, UUIDs and enums as strings.

Anything that cannot be canonicalized (arbitrary Python objects, NaN,
Infinity) is rejected with a clear error — never silently stringified.

## Ignore rules

Ignore rules remove fields that are known to be volatile (generated IDs,
timestamps, auth headers) so they do not break matching. The syntax is
deliberately small:

| Rule | Matches |
|---|---|
| `timestamp` | a key named `timestamp` at any nesting level |
| `headers.authorization` | exactly that dotted location |

List items are traversed but not addressable by index (v0.1 scope).

```python
Canonicalizer(ignore=["timestamp", "request_id", "headers.authorization"])
```

## Fingerprints

The fingerprint of a boundary call is the SHA-256 of the canonical JSON of:

```json
{"boundary_type": "...", "name": "...", "request": {"...": "..."}}
```

It never includes timestamps, span IDs or occurrence indices. Two calls with
the same boundary type, name and canonical request therefore share a
fingerprint regardless of dict ordering or volatile fields.

## Occurrence index

Repeated identical calls are distinguishable. The occurrence index is scoped
by boundary type, name, fingerprint and logical parent, and assigned at
record time in call-start order.

## Matching strategy (v1)

Given an incoming call, the matcher:

1. **Exact fingerprint** — candidates are unconsumed recorded invocations
   with the same fingerprint.
2. **Parent context** — among candidates, prefer those recorded under the
   same logical parent (`parent_name`; span *names*, not IDs, are stable
   across executions).
3. **Occurrence** — among equals, consume the lowest occurrence first.

Each recorded invocation is consumed at most once. There is **no fuzzy
matching during replay** — fuzzy similarity exists only inside mismatch
diagnostics.

## Mismatch diagnostics

When no recorded interaction matches, the replay fails closed with
`ReplayMismatchError`. The message contains:

- the boundary type and name,
- the canonical received request,
- the closest recorded interaction (by textual similarity of the canonical
  JSON), with a similarity percentage,
- a structural difference listing (`+` added keys, `-` removed keys,
  `~` changed values),
- an explicit note that no live call was made and no silent fallback
  occurred.

```text
Replay mismatch: no recorded interaction matches tool.search.search_orders

Received:
{"customer_id":912,"region":"EU"}

Closest recorded interaction (similarity 87%):
{"customer_id":912}

Difference:
  + region = 'EU'

Replay fails closed: no live call was made and no silent fallback occurred.
```

## Unused recordings

Recordings the replay never needed are reported on the result
(`unused_recordings`). They indicate the candidate took a shorter path —
useful signal, reported as a warning-level concern, not a failure.
