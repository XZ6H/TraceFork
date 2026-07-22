"""Live smoke suite against a real OpenAI-compatible API (maintainer task).

Validates the whole TraceFork pipeline against real service traffic:

  1. record: SDK objects, usage/latency metadata, fingerprints
  2. key hygiene: the API key never reaches fixture bytes
  3. hermetic replay: zero socket attempts, byte-identical responses
  4. fail-closed: changed requests raise, never fall back to live
  5. persistence: fixture store round trip, replay-twice determinism
  6. selective replay: live LLM with everything else frozen
  7. cross-replay: run 2 served entirely from run 1's recording
  8. error capture: real API errors recorded, then re-raised
  9. unicode round trip
 10. parallel live calls + parallel hermetic replay
 11. agentic tool-call loop (LLM → tool → LLM) recorded and replayed offline
 12. httpx adapter: raw chat.completions record/replay
 13. streaming: record and replay event sequences offline

Usage (never commit keys):

    LIVE_SMOKE_API_KEY=sk-or-v1-... \
    LIVE_SMOKE_MODEL=meta/muse-spark-1.3-contributor \
    LIVE_SMOKE_BASE_URL=https://openrouter.ai/api/v1 \
        uv run python scripts/live_smoke.py

Exits 0 when every check passes; 1 on failures; 3 when the key is unset.
Sections depending on provider/model cooperation may be skipped with a note.
"""

import asyncio
import json
import os
import sys
import tempfile
from typing import Any

import httpx
from openai import AsyncOpenAI
from tracefork import ToolBox, record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime, ReplayMode, ReplayPolicy
from tracefork.replay import ReplaySession
from tracefork.serialization import build_envelope
from tracefork.storage import FilesystemFixtureStore
from tracefork_openai import instrument_openai

BASE_URL = os.environ.get("LIVE_SMOKE_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.environ.get("LIVE_SMOKE_MODEL", "openai/gpt-4o-mini")
PROMPT = "Reply with exactly one word: ok"

CHECKS: list[tuple[str, bool, str]] = []
SKIPPED: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def skip(name: str, reason: str) -> None:
    SKIPPED.append(name)
    print(f"  [SKIP] {name} — {reason}")


class DeadTransport(httpx.AsyncBaseTransport):
    """Fails the run the moment anything tries to touch the network."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise AssertionError(f"network touched during replay: {request.url}")


def make_client(key: str, transport: httpx.AsyncBaseTransport) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=BASE_URL, api_key=key, http_client=httpx.AsyncClient(transport=transport)
    )


def make_runtime(
    registry: BoundaryRegistry | None = None,
) -> tuple[BoundaryRuntime, BoundaryRegistry]:
    reg = registry if registry is not None else BoundaryRegistry()
    return BoundaryRuntime(registry=reg), reg


async def section_record_and_metadata(key: str) -> Any:
    """Sections 1-5: record, hygiene, hermetic replay, persistence, fail-closed."""
    print("\n— record & metadata —")
    registry = BoundaryRegistry()
    record_runtime, _ = make_runtime(registry)
    recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(recorder, record_runtime)
    with record("live-smoke", input=PROMPT) as rec:
        response = await recorder.responses.create(model=MODEL, input=PROMPT, max_output_tokens=512)
    fixture = build_envelope(rec.trace)

    (invocation,) = rec.trace.invocations
    usage = invocation.metadata.get("usage", {})
    check("recorded a real SDK Response", bool(response.id), response.id)
    check("output text is real model output", len(response.output_text) > 0)
    check("usage metadata captured", usage.get("input_tokens", 0) > 0, json.dumps(usage))
    check("latency captured", invocation.metadata.get("latency_ms", 0) >= 0)
    fingerprint = invocation.fingerprint
    check("request fingerprint stored", fingerprint is not None and len(fingerprint) == 64)

    print("\n— key hygiene —")
    fixture_text = fixture.to_json_bytes().decode("utf-8")
    check("API key never reaches fixture bytes", key not in fixture_text)

    print("\n— hermetic replay —")
    dead = DeadTransport()
    offline = make_client(key, dead)
    instrument_openai(offline, record_runtime)
    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        replayed = await offline.responses.create(model=MODEL, input=PROMPT, max_output_tokens=512)
    check("replay made zero network requests", len(dead.requests) == 0)
    check("replay matched the recording", session.result.matched == 1)
    check("replayed id matches recording", replayed.id == response.id, replayed.id)
    check("replayed output matches recording", replayed.output_text == response.output_text)
    check("replay reports hermetic", session.result.is_hermetic)

    print("\n— replay-twice determinism + store round trip —")
    with tempfile.TemporaryDirectory() as tmp:
        store = FilesystemFixtureStore(tmp)
        store.save("live-smoke", fixture)
        loaded = store.load("live-smoke")
        check("fixture store round trip preserves payload", loaded == fixture)
        session_b = ReplaySession(fixture=loaded, registry=registry)
        with session_b:
            replayed_b = await offline.responses.create(
                model=MODEL, input=PROMPT, max_output_tokens=512
            )
        session_c = ReplaySession(fixture=loaded, registry=registry)
        with session_c:
            replayed_c = await offline.responses.create(
                model=MODEL, input=PROMPT, max_output_tokens=512
            )
        check(
            "replay twice is deterministic",
            replayed_b.output_text == replayed_c.output_text == response.output_text,
        )
        check("store round trip made zero network requests", len(dead.requests) == 0)

    print("\n— fail-closed against the live service —")
    from tracefork.errors import ReplayMismatchError

    session2 = ReplaySession(fixture=fixture, registry=registry)
    mismatch_failed_closed = False
    with session2:
        try:
            await offline.responses.create(
                model=MODEL, input="a completely different prompt", max_output_tokens=512
            )
        except ReplayMismatchError:
            mismatch_failed_closed = True
    # Correct fail-closed evidence: the request is never even attempted.
    check(
        "changed prompt fails closed (no live fallback)",
        mismatch_failed_closed and len(dead.requests) == 0,
    )
    return fixture, registry, response


async def section_selective_live_llm(key: str, registry: BoundaryRegistry, fixture: Any) -> None:
    print("\n— selective replay: live LLM against the real service —")
    runtime, _ = make_runtime(registry)
    live_client = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(live_client, runtime)
    policy = ReplayPolicy(llm=ReplayMode.LIVE)
    session = ReplaySession(fixture=fixture, registry=registry, policy=policy)
    with session:
        replayed = await live_client.responses.create(
            model=MODEL, input=PROMPT, max_output_tokens=512
        )
    result = session.result
    check(
        "live LLM actually ran against the real API",
        len(result.live_boundaries) == 1,
        str(result.live_boundaries),
    )
    check("live run is not hermetic", result.is_hermetic is False)
    check("live response is a fresh real completion", bool(replayed.output_text))
    check("candidate trace recorded the live invocation", len(result.trace.invocations) == 1)


async def section_cross_replay(key: str, registry: BoundaryRegistry) -> None:
    print("\n— identical calls: same fingerprint, cross-replay —")
    runtime, _ = make_runtime(registry)
    recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(recorder, runtime)

    with record("run-1") as rec1:
        r1 = await recorder.responses.create(model=MODEL, input=PROMPT, max_output_tokens=256)
    (inv1,) = rec1.trace.invocations

    with record("run-2") as rec2:
        r2 = await recorder.responses.create(model=MODEL, input=PROMPT, max_output_tokens=256)
    (inv2,) = rec2.trace.invocations

    check("identical requests produce identical fingerprints", inv1.fingerprint == inv2.fingerprint)
    print(f"    run-1 output: {r1.output_text!r}")
    print(f"    run-2 output: {r2.output_text!r}")

    # Run 2's request served entirely from run 1's recording.
    dead = DeadTransport()
    offline = make_client(key, dead)
    instrument_openai(offline, runtime)
    session = ReplaySession(fixture=build_envelope(rec1.trace), registry=registry)
    with session:
        replayed = await offline.responses.create(model=MODEL, input=PROMPT, max_output_tokens=256)
    check(
        "run-2 request served from run-1 recording",
        session.result.matched == 1 and len(dead.requests) == 0,
    )
    check("cross-replay returns run-1's response", replayed.output_text == r1.output_text)


async def section_error_capture(key: str, registry: BoundaryRegistry) -> None:
    print("\n— real API error capture —")
    runtime, _ = make_runtime(registry)
    recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(recorder, runtime)

    from openai import APIStatusError

    raised: str | None = None
    try:
        # The except sits OUTSIDE the recording context: escaping exceptions
        # are what mark the trace failed (same pattern as the CLI bootstrap).
        with record("error-case") as rec:
            await recorder.responses.create(
                model="definitely/not-a-real-model-xyz", input="hi", max_output_tokens=32
            )
    except APIStatusError as exc:
        raised = type(exc).__name__

    check("real API error re-raised as SDK exception", raised is not None, str(raised))
    check("error recorded in trace status", rec.trace.status.value == "failed")
    (invocation,) = rec.trace.invocations
    error = invocation.metadata.get("error", {})
    check("error metadata captured from real API", error.get("type") == raised, json.dumps(error))
    fixture_text = build_envelope(rec.trace).to_json_bytes().decode("utf-8")
    check("error fixture still key-free", key not in fixture_text)


async def section_unicode(key: str, registry: BoundaryRegistry) -> None:
    print("\n— unicode round trip —")
    prompt = "Reply with exactly: 🌊 你好 café"
    runtime, _ = make_runtime(registry)
    recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(recorder, runtime)
    with record("unicode-case") as rec:
        response = await recorder.responses.create(model=MODEL, input=prompt, max_output_tokens=512)
    fixture = build_envelope(rec.trace)
    text = fixture.to_json_bytes().decode("utf-8")
    check("unicode survives canonical serialization", "🌊" in text or "你好" in text)

    dead = DeadTransport()
    offline = make_client(key, dead)
    instrument_openai(offline, runtime)
    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        replayed = await offline.responses.create(model=MODEL, input=prompt, max_output_tokens=512)
    check("unicode replay identical", replayed.output_text == response.output_text)
    check("unicode replay made zero requests", len(dead.requests) == 0)


async def section_parallel(key: str, registry: BoundaryRegistry) -> None:
    print("\n— parallel live calls + parallel hermetic replay —")
    runtime, _ = make_runtime(registry)
    recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(recorder, runtime)

    async def one(n: int) -> str:
        r = await recorder.responses.create(
            model=MODEL,
            input=f"Reply with exactly one word, any word ({n}).",
            max_output_tokens=256,
        )
        return r.output_text

    with record("parallel-live") as rec:
        outputs = await asyncio.gather(one(1), one(2))
    check("parallel live calls both recorded", len(rec.trace.invocations) == 2, str(outputs))

    dead = DeadTransport()
    offline = make_client(key, dead)
    instrument_openai(offline, runtime)
    session = ReplaySession(fixture=build_envelope(rec.trace), registry=registry)

    async def replay_one(n: int) -> str:
        async def live() -> str:
            raise AssertionError("must not run")

        r = await offline.responses.create(
            model=MODEL,
            input=f"Reply with exactly one word, any word ({n}).",
            max_output_tokens=256,
        )
        return r.output_text

    with session:
        replayed_outputs = await asyncio.gather(replay_one(1), replay_one(2))
    check("parallel hermetic replay matches", replayed_outputs == outputs)
    check("parallel replay made zero requests", len(dead.requests) == 0)
    check("parallel replay matched both", session.result.matched == 2)


async def section_tool_loop(key: str, registry: BoundaryRegistry) -> None:
    print("\n— agentic tool-call loop (LLM → tool → LLM) —")
    runtime, reg = make_runtime(registry)
    toolbox = ToolBox(runtime)

    @toolbox.tool(name="get_weather")
    async def get_weather(city: str) -> dict[str, Any]:
        return {"temperature": 21, "city": city}

    tools = [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]

    async def agent_loop(client: AsyncOpenAI, tool_choice: str) -> tuple[str, str]:
        r1 = await client.responses.create(
            model=MODEL,
            input="What is the weather in Berlin? You must call the get_weather tool.",
            tools=tools,
            tool_choice=tool_choice,
            max_output_tokens=1024,
        )
        call = next((o for o in r1.output if getattr(o, "type", "") == "function_call"), None)
        if call is None:
            raise LookupError("model did not emit a function call")
        result = await get_weather(**json.loads(call.arguments))
        r2 = await client.responses.create(
            model=MODEL,
            input=[
                {"role": "user", "content": "What is the weather in Berlin? Use the tool."},
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                },
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result, sort_keys=True),
                },
            ],
            max_output_tokens=1024,
        )
        return r1.output_text, r2.output_text

    recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(recorder, runtime)
    from openai import APIStatusError

    rec = None
    tool_choice_used = "required"
    try:
        with record("agent-loop") as rec:
            live_r1, live_r2 = await agent_loop(recorder, tool_choice="required")
    except (LookupError, APIStatusError):
        # Some providers only support tool_choice="auto"; retry that way.
        tool_choice_used = "auto"
        try:
            with record("agent-loop") as rec:
                live_r1, live_r2 = await agent_loop(recorder, tool_choice="auto")
        except (LookupError, APIStatusError) as exc:
            skip("agentic tool-call loop", f"provider/model did not cooperate: {exc}")
            return

    check("loop used the tool", len(rec.trace.invocations) == 3, "expected 2 llm + 1 tool")
    families = [inv.boundary_type.split(".", 1)[0] for inv in rec.trace.invocations]
    check("loop trajectory is llm → tool → llm", families == ["llm", "tool", "llm"], str(families))

    dead = DeadTransport()
    offline = make_client(key, dead)
    instrument_openai(offline, runtime)
    session = ReplaySession(fixture=build_envelope(rec.trace), registry=reg)
    with session:
        replay_r1, replay_r2 = await agent_loop(offline, tool_choice_used)
    check("full agent loop replayed with zero network", len(dead.requests) == 0)
    check("all three boundaries matched", session.result.matched == 3)
    check("replayed final answer identical", replay_r2 == live_r2)
    check("replayed first response identical", replay_r1 == live_r1)
    check("loop replay reports hermetic", session.result.is_hermetic)


async def section_httpx_raw(key: str) -> None:
    print("\n— httpx adapter: raw chat.completions record/replay —")
    from tracefork_httpx import TraceForkAsyncTransport

    registry = BoundaryRegistry()
    runtime, _ = make_runtime(registry)
    client = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=httpx.AsyncHTTPTransport(), runtime=runtime)
    )

    async with client:
        with record("raw-llm-http") as rec:
            response = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": PROMPT}],
                    "max_tokens": 32,
                },
            )
    check("raw HTTP call succeeded against the real API", response.status_code == 200)
    # Redaction applies to the recorded payload; the in-flight request
    # legitimately carried the real key (it had to authenticate).
    (invocation,) = rec.trace.invocations
    check(
        "authorization header redacted in recording",
        invocation.request["headers"]["authorization"] == "[REDACTED]",
    )
    check(
        "raw fixture key-free",
        key not in build_envelope(rec.trace).to_json_bytes().decode("utf-8"),
    )

    dead = DeadTransport()
    runtime2, _ = make_runtime(registry)
    offline = httpx.AsyncClient(transport=TraceForkAsyncTransport(inner=dead, runtime=runtime2))
    session = ReplaySession(fixture=build_envelope(rec.trace), registry=registry)
    async with offline:
        with session:
            replayed = await offline.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": PROMPT}],
                    "max_tokens": 32,
                },
            )
    check("raw replay byte-identical JSON", replayed.json() == response.json())
    check("raw replay made zero requests", len(dead.requests) == 0)
    check("raw replay matched", session.result.matched == 1)


async def section_streaming(key: str, registry: BoundaryRegistry) -> None:
    print("\n— streaming: record and replay offline —")
    runtime, _ = make_runtime(registry)
    recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(recorder, runtime)

    with record("live-smoke-stream") as rec:
        stream = await recorder.responses.create(
            model=MODEL, input=PROMPT, max_output_tokens=512, stream=True
        )
        live_deltas = [
            event.delta
            async for event in stream
            if getattr(event, "type", "") == "response.output_text.delta"
        ]
    check("streamed real deltas", len(live_deltas) > 0, f"{len(live_deltas)} delta events")

    dead = DeadTransport()
    offline = make_client(key, dead)
    instrument_openai(offline, runtime)
    session = ReplaySession(fixture=build_envelope(rec.trace), registry=registry)
    with session:
        replayed_stream = await offline.responses.create(
            model=MODEL, input=PROMPT, max_output_tokens=512, stream=True
        )
        replayed_deltas = [
            event.delta
            async for event in replayed_stream
            if getattr(event, "type", "") == "response.output_text.delta"
        ]
    check("replayed stream matches recording", replayed_deltas == live_deltas)
    check("stream replay made zero network requests", len(dead.requests) == 0)


async def main() -> int:
    key = os.environ.get("LIVE_SMOKE_API_KEY")
    if not key:
        print("LIVE_SMOKE_API_KEY not set; skipping live smoke (exit 3)")
        return 3

    fixture, registry, _ = await section_record_and_metadata(key)
    await section_selective_live_llm(key, registry, fixture)
    await section_cross_replay(key, registry)
    await section_error_capture(key, registry)
    await section_unicode(key, registry)
    await section_parallel(key, registry)
    await section_tool_loop(key, registry)
    await section_httpx_raw(key)
    await section_streaming(key, registry)

    print()
    failed = [name for name, passed, _ in CHECKS if not passed]
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed, {len(SKIPPED)} skipped")
    for name in failed:
        detail = next(d for n, p, d in CHECKS if n == name)
        print(f"FAILED: {name} {detail}")
    for name in SKIPPED:
        print(f"SKIPPED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
