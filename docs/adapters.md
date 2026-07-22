# Adapter Contract

How integrations connect to TraceFork. The rule (ADR 0004): **adapters
translate, the core decides.** An adapter converts native calls into
canonical boundary data and back — it never implements recording, matching
or replay semantics.

## The two directions

| Direction | Method | When it runs |
|---|---|---|
| native → canonical | `execute(request, call_live)` | RECORD mode and LIVE boundaries: run the real call, return its canonical response. |
| canonical → native | `restore(response, metadata)` | REPLAY boundaries: rebuild the native response object from the recorded payload. |

The runtime guarantees:

- `execute` runs at most once per boundary call, and never during a REPLAY
  boundary — the live callable does not even exist on that path.
- `restore` receives exactly the payload `execute` (or the recording) produced.
- Mode decisions, fingerprinting, matching and fail-closed behavior are
  implemented once in the runtime and hold for every adapter.

## The reference implementation: Python tools

`tracefork.adapters.PythonToolHandler` is the canonical minimal adapter:

```python
class PythonToolHandler:
    async def execute(self, request, call_live):
        return BoundaryResponse(response=await call_live(), metadata={})

    def restore(self, response, metadata):
        return response
```

Tool-specific behavior lives on the calling side: `ToolBox` wraps Python
functions so that invoking the function routes through the boundary runtime
with the tool's fully-qualified name and canonicalized arguments.

```python
registry = BoundaryRegistry()
runtime = BoundaryRuntime(registry=registry)
tools = ToolBox(runtime)  # registers the shared tool handler


@tools.tool(name="search_orders")
async def search_orders(customer_id: int) -> dict: ...


# inside record()/ReplaySession contexts the call is intercepted:
result = await search_orders(customer_id=912)
```

`ToolBox.wrap(func, name=...)` wraps existing functions. Argument
canonicalization accepts primitives, dataclasses, Pydantic models, enums,
datetimes, UUIDs and collections (sets are ordered deterministically);
anything else raises `AdapterError` — objects are never silently stringified.

## Writing an adapter

1. Pick a boundary type: `family.name`, e.g. `llm.openai`, `http.httpx`.
   The family decides the span kind recorded for calls.
2. In the interception point, build the canonical request payload
   (JSON-ready data) and call `runtime.invoke(boundary_type, name, request,
   call_live)`. `call_live` performs the real native call and returns its
   result.
3. Implement `execute` (usually trivial: run `call_live`, wrap the result)
   and `restore` (rebuild whatever SDK object application code expects —
   raw dictionaries are not acceptable if users normally receive SDK
   objects).
4. Register the handler: `registry.register(boundary_type, handler)`.

Design constraints:

- no global registries with hidden mutation; construction is explicit,
- async-first; treat async as first-class, not an afterthought,
- never record secrets (API keys, authorization headers),
- adapter packages depend on core; core never depends on adapters,
- **responses must be JSON-safe**: the runtime canonicalizes every response
  before persisting it; a response containing unsupported objects fails the
  call with `AdapterError` and nothing is written to the trace.

## Roadmap

| Adapter | Milestone |
|---|---|
| Python tools (`tracefork.adapters`) | M6 (done) |
| OpenAI (`tracefork-openai`, Responses API first, streaming separately) | M7 |
| httpx client (`tracefork-httpx`) | M8 |
| LangGraph / OpenAI Agents / MCP | post-v0.1 |
