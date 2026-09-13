# TraceFork

[![CI](https://github.com/XZ6H/TraceFork/actions/workflows/ci.yml/badge.svg)](https://github.com/XZ6H/TraceFork/actions/workflows/ci.yml)
[![Docs](https://github.com/XZ6H/TraceFork/actions/workflows/docs.yml/badge.svg)](https://xz6h.github.io/TraceFork/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Replay AI agent failures locally and turn them into regression tests.

Record a production agent execution once. Then freeze the model, the tools, and the APIs, change your agent, and replay the same world. Compare where the new run diverged. The replay makes no production side effects and no repeated tool calls, and it doesn't flake in CI.

```console
$ tracefork replay refund-failure --live llm
$ tracefork diff refund-failure fixed-run

Trajectory
  llm:planner
  tool:get_order
  tool:get_customer
- tool:refund_order
+ tool:check_refund_policy

First divergence: removed at alignment step 3
```

## Why testing agents is hard

**1. "It worked yesterday."** An agent's behavior depends on the model, the prompt, the tools, the tools' responses, the time of day, and the network. Change any one and behavior shifts. When it breaks, you can't answer what changed or where.

**2. Production failures are unreproducible.** The customer saw the agent refund an expired order. The exact model response that caused it is gone. You can't re-run the incident against production, and re-running locally hits a different model mood.

**3. Agent tests in CI are slow, costly, and flaky.** Every test call hits a priced API, takes seconds, and fails randomly. Teams either skip agent tests or ignore the red builds.

**4. Final-answer checks miss process bugs.** The answer is right, but the agent queried the production database 11 times, called a privileged tool it shouldn't have, or spent 6× the tokens. End-to-end tests only check the answer.

**5. Prompt and model upgrades are rollbacks in the dark.** You swap the prompt, run three examples by hand, shrug, and ship. When regression hits two weeks later, there is no baseline to compare against.

## How TraceFork fixes it

TraceFork records the complete execution as an immutable, Git-friendly fixture: LLM requests and responses, tool calls and results, HTTP traffic, timing, errors. Everything an agent touches becomes a *boundary* that can be frozen or re-run.

| Boundary | Recording | Hermetic replay | Selective replay |
|---|---|---|---|
| LLM call | request + response + usage | served from fixture | runs for real |
| Tool call | arguments + result | served from fixture | runs for real |
| HTTP call | method/URL/body/response | served from fixture | hits the real server |

**Hermetic replay** runs your current agent code with every boundary served from the recording. It is deterministic, offline, and costs zero API tokens. An unmatched call raises an error rather than making a silent request.

**Selective replay** freezes part of the world and re-runs the rest. Test a new prompt against yesterday's tool responses, or new tools against the recorded model decisions.

**Diff** aligns baseline and candidate trajectories and reports the first behavioral divergence plus resource deltas (tokens, cost, calls, latency).

**Assertions and suites** turn any incident into a CI gate: required tools, forbidden tools, ordering rules, call-count and token/cost maximums, with deterministic exit codes.

## Installation

The packages are not on PyPI yet (planned for the next release). Install from source:

```console
git clone https://github.com/XZ6H/TraceFork
cd TraceFork
uv sync
```

Requires Python 3.12+. Works with any OpenAI-compatible API (OpenAI, OpenRouter, vLLM, llama.cpp server, and others).

## Quickstart

```python
import asyncio

from tracefork import ToolBox, record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.replay import ReplaySession
from tracefork.serialization import build_envelope

registry = BoundaryRegistry()
runtime = BoundaryRuntime(registry=registry)
tools = ToolBox(runtime)


@tools.tool(name="weather")
async def weather(city: str) -> dict:
    return {"temperature": 21}  # real call: HTTP, DB, anything


async def main() -> None:
    # 1. Record: the tool runs for real, everything is captured.
    with record("weather-case") as rec:
        await weather("Berlin")
    fixture = build_envelope(rec.trace)  # immutable, digest-sealed

    # 2. Replay later. The real function can be deleted, the server can be
    #    down, the API key can be revoked. The recording answers instead.
    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        assert await weather("Berlin") == {"temperature": 21}
    assert session.result.is_hermetic  # zero live calls, zero network


asyncio.run(main())
```

The same approach works for OpenAI calls (sync, async, and streaming) via [tracefork-openai](docs/adapters.md), and for raw HTTP via [tracefork-httpx](docs/adapters.md).

## Recipes

Each recipe solves a problem you have hit while developing agents. The running example is a customer-support agent that refunds orders, and the production incident where it refunded an expired order because its prompt skipped the policy check. The full walkthrough lives in [examples/support-agent](examples/support-agent).

### Recipe 1: A production failure becomes a regression test

Record the buggy behavior once and freeze it as a fixture. A suite then asserts the fix stays fixed: the policy check must happen, the refund must never run.

```yaml
# tests/support-agent.yaml
name: support-agent-regressions
cases:
  - name: expired-order-rejected
    fixture: fixtures/fixed-run.json
    entrypoint: support_agent.agent:run
    expect:
      tools:
        require: [check_refund_policy]
        forbid: [refund_order]
```

```console
$ tracefork eval tests/support-agent.yaml
support-agent-regressions

1 case(s)

  PASS expired-order-rejected

1 passed
0 failed
```

If anyone reintroduces the direct refund, the suite fails with the evidence: `forbidden tool refund_order: called 1 time(s)`. Exit code 1, CI red.

### Recipe 2: "It worked yesterday." Find what changed.

Two runs, one `tracefork diff` command. The output shows which tool calls were removed, which were added, and at which step the paths split.

```console
$ tracefork diff incident-1821.json fixed-run.json

Trajectory
  llm:planner
  tool:get_order
  tool:get_customer
- tool:refund_order
+ tool:check_refund_policy

First divergence: removed at alignment step 3

Resources
  tool_calls: 3 -> 3 (+0.0%)
```

### Recipe 3: Test a new prompt against yesterday's tool responses

You rewrote the prompt. Run the model live while the tools stay frozen at their recorded responses. The only variable is the prompt.

```python
from tracefork.boundaries import ReplayMode, ReplayPolicy

policy = ReplayPolicy(llm=ReplayMode.LIVE)  # model runs for real, tools frozen
session = ReplaySession(fixture=fixture, registry=registry, policy=policy)
with session:
    await agent.run(input_data)
```

Live calls are counted and reported. Everything else is served from the recording. Flip it around to test a migrated tool against the recorded model decisions:

```python
policy = ReplayPolicy(families={"tool": ReplayMode.LIVE})  # tools live, LLM frozen
```

### Recipe 4: Deterministic error handling

The recording captured a boundary failing (a 400 from the provider, a tool crash). Replay reproduces that failure exactly, so you can test your error handling without anyone causing real errors:

```python
from tracefork.errors import ReplayRecordedError

session = ReplaySession(fixture=fixture_with_recorded_error, registry=registry)
with session, pytest.raises(ReplayRecordedError) as excinfo:
    await agent.run(input_data)

assert excinfo.value.recorded_type == "BadRequestError"
```

Your `except` branch runs instead of the live dependency.

### Recipe 5: Mock a boundary entirely

A dependency is too flaky or expensive to record. Serve a fixed payload from the policy instead. Keyed by boundary name, with no live call and no recording needed:

```python
policy = ReplayPolicy(
    tools={"get_payment_status": ReplayMode.MOCK},
    mocks={"get_payment_status": {"status": "declined"}},
)
session = ReplaySession(fixture=fixture, registry=registry, policy=policy)
```

Mock calls are recorded in the candidate trace like any other boundary. A name with no configured mock fails closed rather than hitting the network.

### Recipe 6: Gate resources, not only correctness

The answer is correct. The token count might not be. Resource maximums run alongside the structural assertions:

```yaml
expect:
  max:
    tool_calls: 4
    llm_calls: 2
    total_tokens: 10000
    cost_usd: 0.05
```

Machine-readable runs for CI dashboards: `tracefork eval suite.yaml --format json` or `--format junit`.

### Recipe 7: Record and replay raw HTTP

Non-SDK dependencies like REST APIs and webhooks go through the httpx adapter. Secret headers are redacted before anything is written, and binary bodies replay byte-identically:

```python
import httpx

from tracefork_httpx import TraceForkAsyncTransport

client = httpx.AsyncClient(
    transport=TraceForkAsyncTransport(inner=httpx.AsyncHTTPTransport(), runtime=runtime)
)
async with client:
    with record("api-case") as rec:
        await client.post("https://payments.example.test/capture", json={"id": 1})
```

Replays of the same request are served from the recording without opening a socket.

## The CLI

```console
tracefork init                                        # project layout
tracefork record --name incident python run.py        # record a script
tracefork inspect .tracefork/fixtures/incident.json   # summarize a fixture
tracefork replay fixture.json --entrypoint app:run --live llm
tracefork diff baseline.json candidate.json           # regression report
tracefork eval suite.yaml                             # CI regression gate
```

Every command documents its options in `--help`. The full contract, including the CI exit codes (0 pass, 1 regression, 2 execution error, 3 invalid input), is in [docs/cli.md](docs/cli.md).

## What gets recorded

- LLM requests and responses (OpenAI Responses API: sync, async, streaming, with usage, latency, and finish reason)
- Tool calls: fully-qualified name, canonicalized arguments, result, errors
- HTTP traffic: method, URL, headers (secrets redacted), status, bodies
- Span tree: nested and parallel work, timing, exceptions
- Provenance: git commit, dirty status, branch

Everything is redacted before persistence, sealed with a SHA-256 digest, and stored as a single versioned JSON file you can commit.

## Guarantees

The test suite ([docs/testing.md](docs/testing.md)) enforces these:

- Hermetic replay makes zero external calls. The tests use transports that explode on any socket attempt.
- Every replayed boundary corresponds to exactly one recorded interaction.
- The same fixture and code produce the same replay, every time.
- A replay never mutates the fixture.
- Unmatched calls fail closed with diagnostics. They never fall back to the real dependency.

## Docs

| | |
|---|---|
| [Concepts](docs/concepts.md) | traces, spans, boundaries, replay modes |
| [Architecture](docs/architecture.md) | package layers and data flow |
| [Replay semantics](docs/replay-semantics.md) | policies, guarantees, MOCK mode |
| [Matching](docs/matching.md) | fingerprints, occurrence handling, diagnostics |
| [CLI reference](docs/cli.md) | commands, options, exit codes, suite schema |
| [Failure model](docs/failure-model.md) | error hierarchy, fail-closed rules |
| [Adapters](docs/adapters.md) | the integration contract |
| [Security](docs/security.md) | redaction, integrity, key handling |
| [Roadmap](docs/roadmap.md) | what ships next |

## Demo

[examples/support-agent](examples/support-agent) is a complete, offline walkthrough of the core story: a support agent refunds an expired order (incident-1821), the incident is recorded, the fixed agent is replayed against it and fails closed, the diff shows the exact behavioral change, and the regression suite locks the fix in.

## Status

v0.1.0 is released and CI-gated. Next, per the [roadmap](docs/roadmap.md): fork replay, fault injection, OpenTelemetry import, and framework adapters.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```console
uv sync
uv run pytest
```

Contributors and AI agents: read [AGENTS.md](AGENTS.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache-2.0](LICENSE) - Copyright 2026 The TraceFork Authors
