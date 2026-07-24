---
name: gates
description: Run TraceFork's full quality gates (tests, coverage, lint, format, types) and fix any violations the right way. Use before committing, or when the user asks to verify, validate, or check the project.
---

# TraceFork quality gates

Run from the repo root:

```console
uv run pytest -q
uv run pytest --cov=packages -q        # coverage must stay 100%
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/
```

All five must be green before a commit. The CI matrix additionally runs
Ubuntu/Windows x Python 3.12/3.13.

## Fix rules

- **Failing test**: fix the *code* if the test pins correct behavior; fix the
  *test* only if it pins wrong behavior. Say which and why.
- **Lint/type findings**: fix them properly. Never add blanket ignores. A
  `# pragma: no cover` requires an inline comment proving the line is
  unreachable (example: `diff.py` Myers bound).
- **Coverage below 100%**: get the missed lines with
  `uv run pytest --cov=packages --cov-report=term-missing -q`, then add
  targeted tests for each, or delete provably dead code.
- **Warnings are errors** (`filterwarnings = ["error"]`). Never suppress
  globally; fix the source (e.g. unclosed resources).

Re-run all gates until green, then commit small and conceptual.
