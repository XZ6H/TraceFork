# Security

How TraceFork handles secrets, PII and fixture integrity (TF-170..173).

## Redaction happens before persistence (TF-172)

Sensitive values are stripped **when data is recorded**, not when a fixture
is later read. A secret that flows through a recorded boundary call never
reaches the trace, the fixture file, or disk:

- span inputs and outputs,
- boundary invocation requests, responses and metadata.

Key-based redaction is **on by default** with a conservative list:

```text
authorization, api_key, token, secret, password, cookie
```

Matching is case-insensitive and applies at every nesting depth. Matched
values are replaced with `[REDACTED]`.

## Custom redactors (TF-171)

```python
@tracefork.redactor
def scrub_customer(data):
    if isinstance(data, dict) and "customer_email" in data:
        return {**data, "customer_email": "[SCRUBBED]"}
    return data


with record("case", redact=RedactionEngine(redactors=[scrub_customer])):
    ...
```

Custom redactors run after key redaction and receive the whole value tree.
Additional key names can be passed as `record(..., redact=["email"])` — they
are added to the defaults. Pass `RedactionEngine(keys=[])` to disable
key-based redaction entirely (not recommended).

## What is never recorded

- API keys and authorization headers (adapter-level: the OpenAI adapter
  records request payloads only; client credentials are never part of them;
  the httpx adapter redacts `Authorization`, `Cookie`, `Set-Cookie`,
  `X-API-Key`, `Proxy-Authorization` by default),
- stack traces (only exception type and message — traces leak local paths),
- anything matched by the redaction engine above.

## Fixture integrity (PRD §30)

Fixtures are sealed with a SHA-256 digest over their canonical serialization
(see [trace-schema.md](trace-schema.md)). Loading a fixture whose digest does
not match raises `FixtureCorruptError` before anything is replayed. Never
hand-edit fixture files; re-record instead.

## Fail-closed replay

Security-relevant by construction: an unmatched replay call is an error, not
a live call. See [ADR 0003](../adr/0003-fail-closed-replay.md) and
[failure-model.md](failure-model.md).

## Known limitations (v0.1)

- Key-based redaction is exact-match on key names; values that *look* like
  secrets under other keys need a custom redactor.
- Streaming LLM responses are buffered fully in memory at record and replay
  time.
- Fixture signing (cryptographic authentication of fixtures) is a planned
  later feature (PRD §30).
