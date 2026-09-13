# Roadmap

The ordered backlog after v0.1, distilled from the implementation plan
(§39–§43). Each item is scoped enough for an agent (or human) to pick up:
the "done" line is the acceptance boundary. New work should fit the
invariants in [AGENTS.md](../AGENTS.md) — anything that bends one needs an
ADR and a human decision first.

## v0.2 — fork replay + fault injection

### Fork replay (plan TF-200..202)

Re-executes a historical trace from an arbitrary span: the prefix is served
from the recording, everything after the fork point runs under the current
policy.

- Every replayable span gets stable, addressable identity:
  `tracefork fork fixture.json --from span_42`.
- Historical prefix restoration: `A → B → C → D → E`, fork from D replays
  A/B/C and runs D/E under current policy.
- Candidate provenance records the source fixture and fork span.
- Done when: a forked replay matches the recording up to the fork span,
  diverges after it, and the divergence is reported by `tracefork diff`.

### Fault injection (plan TF-210..213)

Deterministic failure injection at boundaries:
`tracefork replay case --fault tool.search=timeout`.

- `Fault` abstraction (timeout, exception, HTTP 429/500, malformed JSON,
  empty response, delay) replacing the recorded response or the live call.
- Faults are themselves deterministic unless configured otherwise.
- Done when: a faulted replay lets error-handling code be tested without
  touching real dependencies, and unconfigured faults still fail closed.

## v0.3 — imports + framework adapters

### OpenTelemetry import (plan TF-220..221)

Convert external OTel spans into TraceFork traces. Distinguish an
*observable trace* from a *replay-capable fixture*: report replay coverage
(LLM request/response captured? tool arguments captured?) as a percentage.
Done when: an exported OTel trace loads with `tracefork inspect` and
non-replayable traces are labeled, not silently accepted.

### Framework adapters (plan TF-230..232)

LangGraph, OpenAI Agents SDK, MCP — via the adapter contract
([adapters.md](adapters.md)). Done per adapter when the standard adapter
test set (record/replay hermetic, mismatch, secret hygiene) passes.

## v0.4 — constraint-based trajectory evaluation (plan TF-240..243)

Beyond require/forbid: partial orderings over multiple steps, forbidden
subsequences, min/max count windows, and set-based matching modes
(strict/ordered/unordered matching modes). Extends `TraceExpectations` and
the suite YAML. Done when the strict/ordered/unordered examples are expressible in suite
YAML and enforced with evidence.

## v0.5 — attribution + matrix

- Change provenance comparison: given baseline and candidate, report which
  surfaces changed (code/prompt/model/tools) using recorded provenance.
- Regression surface classifier: "first divergence + changed surfaces" —
  explicitly *surface*, never root cause.
- Regression matrix: controlled old/new combinations across dimensions with
  automated bisection and the `tracefork diagnose` report.

## Open backlog (unscheduled)

- **config.yaml wiring**: `tracefork init` writes a reserved template; wire
  global canonicalization ignore rules and regression thresholds to it
  (currently suite-level only). Tracked in the template comments.
- Fixture signing: cryptographic authentication of fixtures.
- GitHub PR comment with trajectory diff (TF-181 PR-comment half; the JUnit
  and JSON formats are done).
- Stateful simulated tools / counterfactual responses (research).
- Web trajectory viewer (local-only, no SaaS).
- Performance beyond the current 10× envelope: streaming that exceeds the
  in-memory buffer, 100k+ span traces.

## Ground rules for any of the above

1. TDD per [AGENTS.md](../AGENTS.md) — failing test first, 100% coverage
   maintained, gates green.
2. Core stays framework-independent; new external I/O goes through the
   boundary abstraction ([adapters.md](adapters.md)).
3. Fail-closed always: a new mode or feature that cannot fulfill its promise
   raises, it never falls back to live.
4. Docs ship with the behavior: update the relevant page and the CHANGELOG
   in the same change.
