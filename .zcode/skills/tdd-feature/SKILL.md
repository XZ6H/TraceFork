---
name: tdd-feature
description: The repository's TDD loop for implementing any behavioral change or fix in TraceFork. Use when implementing a feature, fixing a bug, or changing replay/matching/diff semantics.
---

# TraceFork TDD loop

Behavioral changes follow strict red-green gates. Never write the
implementation first.

1. **Pin the behavior** — write the failing test(s) first, named after the
   plan task (e.g. `TF-131`) or the invariant. Place them by category:
   `tests/unit`, `tests/concurrency`, `tests/replay` (invariants),
   `tests/integration`, `tests/property` (Hypothesis), `tests/performance`.
2. **Confirm red** — run the new tests and check they fail *for the right
   reason* (assertion about the missing behavior, not an import error you
   forgot).
3. **Green** — minimal implementation. Respect the invariants in AGENTS.md
   (fail-closed, adapters translate, no global state, error hierarchy).
4. **Gates** — `uv run pytest -q`, `--cov=packages` (100%), ruff check +
   format, `uv run mypy packages/`.
5. **Commit** — small and conceptual, e.g.
   `feat(replay): ...`, `test(replay): verify ...`, `fix(cli): ...`.

Notes:

- Changes to replay semantics or the fixture schema almost always need an
  ADR or docs update in the same commit.
- Concurrency behavior gets dedicated tests under `tests/concurrency`
  (parallel gather, cancellation, isolation) — not a single happy path.
- If a test reveals a bug in an existing invariant, stop and fix the
  invariant first; the feature waits.
