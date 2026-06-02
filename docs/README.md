# TraceFork Documentation

Documents are added with the milestone that introduces the behavior they
describe — no speculative prose. The mapping:

| Document | Introduces | Milestone |
|---|---|---|
| [concepts.md](concepts.md) | Traces, spans, boundaries, fixtures, policies | M1–M5 |
| [trace-schema.md](trace-schema.md) | Trace and fixture JSON schemas, versions, integrity | M1 |
| [matching.md](matching.md) | Canonicalization, fingerprints, occurrence matching, diagnostics | M4 |
| [replay-semantics.md](replay-semantics.md) | Execution modes, policy resolution, hermetic guarantees | M5 |
| [failure-model.md](failure-model.md) | Error hierarchy and failure semantics | M5 |
| [adapters.md](adapters.md) | Boundary/adapter contract for integrations | M6 |
| [architecture.md](architecture.md) | Package layers and runtime | M6 |
| [trajectory-diff.md](trajectory-diff.md) | Alignment and divergence detection | M13 |
| [security.md](security.md) | Redaction, secret handling, fixture integrity | M17 |

Architecture decisions are in [adr/](../adr/).
