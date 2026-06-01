# Security Policy

## Reporting a vulnerability

Do **not** open a public issue for security problems.

Report vulnerabilities through GitHub's private vulnerability reporting
(Security → Report a vulnerability) on this repository. Include a description,
the impact, and a minimal reproduction (redact any secrets from fixtures before
attaching them).

You can expect an initial response within 7 days.

## Scope

Of particular interest:

- anything that would make hermetic replay perform a real external call,
- secrets or PII persisting into fixtures despite redaction,
- fixture integrity bypasses (corrupted fixtures loading without error),
- path traversal or code execution via crafted fixture files.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | yes       |

## Design commitments

TraceFork fails closed on replay mismatches, redacts known secret headers and
keys before persistence, and verifies fixture integrity via SHA-256 digests.
These are documented in [adr/0003-fail-closed-replay.md](adr/0003-fail-closed-replay.md)
and will be documented in `docs/security.md`.
