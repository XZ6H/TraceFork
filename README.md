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

## 30 seconds with the SDK

```python
from tracefork import ToolBox, record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.serialization import build_envelope
from tracefork.replay import ReplaySession

registry = BoundaryRegistry()
runtime = BoundaryRuntime(registry=registry)
tools = ToolBox(runtime)


@tools.tool(name="weather")
async def weather(city: str) -> dict:
    return {"temperature": 21}  # real call: HTTP, DB, anything


with record("weather-case") as rec:
    await weather("Berlin")

fixture = build_envelope(rec.trace)  # committed, digest-sealed
```

Later — with the real function deleted, the server down, the API key revoked:

```python
session = ReplaySession(fixture=fixture, registry=registry)
with session:
    assert await weather("Berlin") == {"temperature": 21}  # replayed, zero live calls
```

Change the world (`weather("Munich")`) and the replay fails closed with
diagnostics instead of calling out.

## The CLI

```console
$ tracefork init                                   # .tracefork/ layout
$ tracefork record --name incident python run.py   # record a script
$ tracefork inspect .tracefork/fixtures/incident.json
$ tracefork replay fixture.json --entrypoint app:run --live llm
$ tracefork diff incident.json fixed.json          # - refund_order / + check_policy
$ tracefork eval suite.yaml                        # CI regression suite (exit codes)
```

## Status

v0.1 milestone set complete:

- [x] Trace model, versioned fixtures, canonical serialization, integrity digests
- [x] Recording engine (contextvars, nested + parallel spans, exception capture)
- [x] Boundary runtime (record/replay modes, fail-closed matching)
- [x] Canonicalization, fingerprints, occurrence-aware matcher, mismatch diagnostics
- [x] Hermetic + selective replay (`--live llm|http|tools`, per-tool overrides)
- [x] Adapters: Python tools, OpenAI Responses API (sync/async/streaming), httpx
- [x] CLI: init / record / inspect / replay / diff / eval
- [x] Metrics + cost model, trajectory diff with first divergence
- [x] Assertions, YAML eval suites, JSON + JUnit output, CI exit codes
- [x] Redaction before persistence, fixture integrity verification
- [x] [Support-agent incident demo](examples/support-agent) (offline, no API keys)

Next: fork replay, fault injection, OTel import, framework adapters, regression
attribution (plan v0.2+).

## Documentation

- [docs/](docs/) — concepts, architecture, replay semantics, matching, failure model,
  adapter contract, trajectory diff, security
- [adr/](adr/) — architectural decision records
- [examples/support-agent](examples/support-agent) — end-to-end incident demo
- [CHANGELOG.md](CHANGELOG.md)

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```console
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/
```

## License

[Apache-2.0](LICENSE)
