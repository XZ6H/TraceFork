# Contributing to TraceFork

Thanks for your interest in contributing. TraceFork optimizes for depth,
correctness and documentation rather than feature count — please keep that in
mind when proposing changes.

## Development setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```console
git clone <your-fork>
cd tracefork
uv sync
uv run pytest
```

## Quality gates

Every PR must pass all of:

```console
uv run pytest                    # tests, warnings are errors
uv run ruff check .              # lint
uv run ruff format --check .     # formatting
uv run mypy packages/            # strict type check
```

Rules enforced in CI:

- warnings are never ignored globally,
- no blanket type ignores,
- no skipped tests without a documented reason.

## Pull requests

Every functional PR must contain:

- the implementation,
- tests,
- documentation if behavior changes.

"Tests coming later" is not accepted.

## Commits

Prefer small conceptual commits following conventional-commit style:

```text
feat(core): add trace and span models
test(replay): verify unmatched calls fail closed
docs(adr): record decision on boundary abstraction
```

## Design constraints

The core package must not depend on any agent framework. Framework-specific
code lives behind adapters in their own packages. Replay must fail closed: an
unmatched recorded interaction is an error, never a silent live call.

If your change would touch these constraints, open an issue first — most likely
it needs an ADR.

## License

By contributing, you agree that your contributions will be licensed under the
Apache License 2.0.
