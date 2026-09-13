# AGENTS.md

Instructions for AI coding agents working in this repository. Humans: most of
this applies to you too — see [CONTRIBUTING.md](CONTRIBUTING.md).

## What this repo is

TraceFork records AI-agent executions (LLM calls, tools, HTTP) as immutable,
versioned fixtures and replays them under an explicit policy, so production
failures can be reproduced locally and turned into deterministic regression
tests. Python 3.12+, uv workspace monorepo.

Read in this order: [README.md](README.md) → [docs/concepts.md](docs/concepts.md) →
[docs/architecture.md](docs/architecture.md) → [adr/](adr/).

## Non-negotiable invariants

These are architectural constraints (ADR 0001–0005). A change that
would violate one requires an explicit human decision and a new ADR — do not
"just implement it".

1. **Framework independence.** `tracefork-core` never imports an agent
   framework. Framework code lives in adapter packages only.
2. **Fail-closed replay.** An unmatched replayed call raises
   `ReplayMismatchError`. No code path turns a replayed call into a live
   call — `ReplaySession.replay_invoke` does not even accept a live callable.
3. **Explicit modes.** LIVE/REPLAY/MOCK/FAULT are resolved only through
   `ReplayPolicy` (exact tool rule → family rule → default). Unconfigured
   MOCK/FAULT fail closed.
4. **Versioned fixtures.** `fixture_version` and `schema_version` are
   validated on load. Within a version the schema is append-only; anything
   else requires a migration and a version bump (ADR 0002).
5. **One boundary abstraction.** Recording and replay share
   `BoundaryRuntime`. Adapters translate (`execute`/`restore`) and never
   decide modes. Requests and responses must be canonicalizable (JSON-safe);
   non-canonicalizable data fails with `AdapterError` before persistence.
6. **Redaction before persistence.** Secrets never reach disk. The default
   secret-key engine is always on; redaction happens at write time, never at
   read time.
7. **Error hierarchy.** Everything raises a `TraceForkError` subclass
   ([docs/failure-model.md](docs/failure-model.md)). Underlying library
   exceptions are wrapped, never leaked as the public API.
8. **No global mutable state.** Execution state lives in `contextvars`. The
   single documented exception is `tracefork.bootstrap`, a service locator
   for CLI subprocess recording.

## Commands (run from repo root)

```console
uv sync                                  # set up / update the environment
uv run pytest -q                         # full suite (~336 tests)
uv run pytest --cov=packages -q          # coverage — must stay 100%
uv run ruff check .                      # lint
uv run ruff format --check .             # formatting
uv run mypy packages/                    # strict type check
```

All gates must pass before every commit.

## TDD workflow (expected here)

1. Write the failing test that pins the behavior. Run it. Confirm it fails
   **for the right reason**.
2. Implement the minimal change. Confirm green.
3. Run all gates. Fix lint/types.
4. Commit small and conceptual: `feat(scope): ...`, `test: ...`, `docs: ...`.

Never weaken a test to make it pass. Never add blanket ignores. Warnings are
errors. Coverage is 100% line coverage — new code ships with tests that cover
it, or it doesn't ship. A `# pragma: no cover` requires an inline comment
proving the line is unreachable.

## Package map

| Package | Contents |
|---|---|
| `packages/tracefork-core` | Engine: models, recording, boundaries, canonicalization, replay, metrics, diff, assertions, redaction, storage |
| `packages/tracefork-cli` | Typer CLI (`init/record/inspect/replay/diff/eval`) + eval suites |
| `packages/tracefork-openai` | OpenAI Responses API adapter (sync/async/streaming) |
| `packages/tracefork-httpx` | httpx transport adapter (async + sync) |

Tests live in `tests/` by risk category: `unit/`, `concurrency/`, `replay/`
(invariants), `integration/`, `property/` (Hypothesis), `performance/` —
the full matrix is in [docs/testing.md](docs/testing.md).

## Where does X go?

| I want to... | Location | Also update |
|---|---|---|
| Add an adapter / provider | adapter package, see [docs/adapters.md](docs/adapters.md) + `add-adapter` skill | docs/adapters.md row |
| Add a CLI command or option | `packages/tracefork-cli/src/tracefork_cli/commands/`, register in `main.py` | [docs/cli.md](docs/cli.md) |
| Add an assertion type | `core/assertions.py` (`TraceExpectations` + evaluator) | `docs/failure-model.md` if it fails |
| Add a metric | `core/metrics.py` (`TraceMetrics` + `extract_metrics`) | test with real-shape usage metadata |
| Add an error type | `core/errors.py` hierarchy | [docs/failure-model.md](docs/failure-model.md) tree + remedies |
| Add a storage backend | `core/storage/` behind the `FixtureStore` protocol | reuse `validate_fixture_name` |
| Add a replay-mode branch | `core/boundaries/runtime.py` MOCK/FAULT pattern, `session.py` | fail-closed test + [docs/replay-semantics.md](docs/replay-semantics.md) |
| Change the fixture schema | [adr/0002](adr/0002-versioned-trace-schema.md) rules | [docs/trace-schema.md](docs/trace-schema.md) |
| Add a docs page | `docs/` | this table + `docs/README.md` index |
| Pick up a task | [docs/roadmap.md](docs/roadmap.md) | invariants above still apply |

## Recipes

### Add a new adapter

Follow [.zcode/skills/add-adapter/SKILL.md](.zcode/skills/add-adapter/SKILL.md)
and [docs/adapters.md](docs/adapters.md). In short: boundary type
`family.name`, a handler with `execute`/`restore`, registration, hermetic
replay test, mismatch test, secret-hygiene test, docs row.

### Add or change an error type

Extend the hierarchy in `errors.py`, update the tree and the
causes/remedies table in [docs/failure-model.md](docs/failure-model.md).

### Change the trace schema

Read [adr/0002-versioned-trace-schema.md](adr/0002-versioned-trace-schema.md)
first. Append-only within `1.0`; bump and migrate otherwise.

### Validate against a real provider

[.zcode/skills/live-smoke/SKILL.md](.zcode/skills/live-smoke/SKILL.md).
Keys come from environment variables only — never commit them, never echo
them into fixtures.

## Prohibited without explicit human approval

New dependencies in `tracefork-core`; agent-framework imports in core;
React/Next.js, Postgres, Redis, Kafka, Docker, Kubernetes, authentication,
hosted services, billing; weakening tests, gates, or the fail-closed
guarantees.

## Commits

Small and conceptual, conventional-commit style:

```text
feat(core): add trace and span models
test(replay): verify unmatched calls fail closed
docs(adr): record decision on boundary abstraction
```
