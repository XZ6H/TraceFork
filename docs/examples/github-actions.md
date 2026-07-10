# Example: TraceFork regression suite in GitHub Actions (TF-180)

Copy this workflow into your agent repository. The suite is hermetic: no
network, no model cost, deterministic verdicts.

```yaml
name: Agent regression tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  agent-regressions:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: uv sync

      - name: Run TraceFork regression suite
        run: uv run tracefork eval tests/agent.yaml

      - name: Publish failure summary
        if: failure()
        run: uv run tracefork eval tests/agent.yaml --format text >> $GITHUB_STEP_SUMMARY
```

## Exit codes

`tracefork eval` maps suite outcomes to CI-friendly exit codes:

| Code | Meaning |
|------|---------|
| 0    | all cases passed |
| 1    | regression (assertion or replay mismatch) |
| 2    | execution error (entrypoint crashed) |
| 3    | invalid suite or fixture |

## Machine-readable output

```console
$ tracefork eval tests/agent.yaml --format json    # structured results
$ tracefork eval tests/agent.yaml --format junit   # CI test reporting
```

## Keeping fixtures fresh

Fixtures are committed artifacts. When a *legitimate* behavior change lands,
re-record the affected fixture in the same PR — the diff review then shows
exactly how the agent's trajectory changed.
