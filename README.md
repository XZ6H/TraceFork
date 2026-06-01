# TraceFork

Replay AI agent failures locally and turn them into regression tests.

Record a production execution once. Then freeze its external dependencies, change
your agent, and replay the same world:

```console
$ tracefork replay refund-failure --live llm
```

Compare exactly where the candidate execution diverged:

```text
✓ get_customer
✓ check_policy
- refund_order
+ escalate_case
```

No production side effects. No repeated tool calls. No flaky CI when using
hermetic replay.

## How it works

Agents are nondeterministic: models, tool outputs, external APIs, clocks and
randomness all feed into every run. Snapshot-testing that output is brittle, and
checking only the final answer hides regressions like redundant tool calls,
privileged tool misuse, or cost explosions.

TraceFork records the complete execution — LLM requests and responses, tool
calls, state transitions, retries, errors and timing — as an immutable,
Git-friendly fixture. Replays are then executed under an explicit policy that
decides, per boundary, whether it runs live or is served from the recording.
Baseline and candidate trajectories are aligned to find what changed and where
the candidate first diverged.

Replay fails closed: if a recorded interaction cannot be matched, the replay
fails with diagnostics. It never silently calls the real dependency.

## Status

Work in progress, built milestone by milestone (TDD, small conceptual commits).

- [x] M0 — repository foundation (uv workspace, quality gates, CI, ADRs)
- [ ] M1 — trace model and persistence
- [ ] M2 — recording engine
- [ ] M3 — boundary abstraction
- [ ] M4 — canonicalization and matching
- [ ] M5 — hermetic replay
- [ ] M6 — Python tool adapter (first vertical slice)
- [ ] M7+ — OpenAI adapter, httpx adapter, selective replay, CLI, diff, eval suites

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```console
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/
```

## Documentation

- [adr/](adr/) — architectural decision records
- [docs/](docs/) — design documents, added as the behavior they describe lands
- [CHANGELOG.md](CHANGELOG.md)

## License

[Apache-2.0](LICENSE)
