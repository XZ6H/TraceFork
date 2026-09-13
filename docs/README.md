# TraceFork Documentation

Documents are added with the milestone that introduces the behavior they
describe.

| Document | Contents |
|---|---|
| [concepts.md](concepts.md) | Traces, spans, boundaries, fixtures, policies, execution modes |
| [architecture.md](architecture.md) | Package layers, data flow, layering rules |
| [trace-schema.md](trace-schema.md) | Trace and fixture JSON schemas, versions, integrity |
| [matching.md](matching.md) | Canonicalization, fingerprints, occurrence matching, diagnostics |
| [replay-semantics.md](replay-semantics.md) | Execution modes, policy resolution, hermetic guarantees |
| [failure-model.md](failure-model.md) | Error hierarchy and fail-closed semantics |
| [adapters.md](adapters.md) | Boundary/adapter contract for integrations |
| [trajectory-diff.md](trajectory-diff.md) | Alignment, resource deltas, first divergence |
| [security.md](security.md) | Redaction, secret handling, fixture integrity |
| [examples/github-actions.md](examples/github-actions.md) | Running `tracefork eval` in CI |
| [cli.md](cli.md) | CLI commands, options, exit codes, suite schema |
| [testing.md](testing.md) | Test plan and coverage matrix |
| [roadmap.md](roadmap.md) | Ordered backlog after v0.1 with done-criteria |

Architecture decisions are in [adr/](adr/); the end-to-end demo lives in
[examples/support-agent](https://github.com/XZ6H/TraceFork/tree/main/examples/support-agent).
