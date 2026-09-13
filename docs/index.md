---
hide:
  - navigation
---

# TraceFork

Replay AI agent failures locally and turn them into regression tests.

Record a production execution once. Then freeze its external dependencies,
change your agent, and replay the same world:

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

## The 30-second version

```python
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

Change the world (`weather("Munich")`) and the replay **fails closed** with
diagnostics instead of calling out.

## Where to go next

- [Concepts](concepts.md) — traces, spans, boundaries, replay modes
- [CLI](cli.md) — every command and the CI exit-code contract
- [Replay semantics](replay-semantics.md) — what a green hermetic replay guarantees
- [Adapters](adapters.md) — how integrations plug in (Python tools, OpenAI, httpx)
- [Roadmap](roadmap.md) — what ships next
- [Support-agent demo](https://github.com/XZ6H/TraceFork/tree/main/examples/support-agent) — a full incident walkthrough
