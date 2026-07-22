"""Live smoke suite against a real OpenAI-compatible API (maintainer task).

100+ checks validating the whole TraceFork pipeline against real service
traffic. Assertions target the *mechanics* (record fidelity, matching,
hermeticity, metadata) so the suite never flakes on model creativity.

Sections:
  1. record & metadata, key hygiene, hermetic replay, persistence, fail-closed
  2. selective replay: live LLM against the real service
  3. cross-replay: run 2 served from run 1's recording
  4. real API error taxonomy (3 error shapes)
  5. unicode round trip
  6. parallel live calls + parallel hermetic replay
  7. agentic tool-call loop (LLM -> tool -> LLM), offline replay
  8. httpx adapter: raw chat.completions record/replay
  9. streaming deep dive: deltas, completed event, usage, mismatch
 10. parameter matrix: temperature, sampling, metadata, instructions
 11. long output and JSON output payloads
 12. matching precision: model change, whitespace change, occurrence exhaustion
 13. ignore rules with a genuinely volatile request field
 14. chained conversation: replayed output feeds the next input
 15. two-tool branch selection
 16. metrics, assertions and diff over real traces
 17. policy edges: default-LIVE, MOCK unsupported
 18. fixture integrity tampering on real payloads

Usage (never commit keys):

    LIVE_SMOKE_API_KEY=sk-or-v1-... \
    LIVE_SMOKE_MODEL=meta/muse-spark-1.3-contributor \
    LIVE_SMOKE_BASE_URL=https://openrouter.ai/api/v1 \
        uv run python scripts/live_smoke.py

Optionally filter sections: LIVE_SMOKE_ONLY=record,streaming

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
from tracefork.assertions import ResourceMaximums, TraceExpectations, evaluate_expectations
from tracefork.boundaries import (
    BoundaryRegistry,
    BoundaryRuntime,
    ReplayMode,
    ReplayPolicy,
)
from tracefork.canonicalization import Canonicalizer
from tracefork.diff import diff_traces
from tracefork.errors import ReplayMismatchError, ReplayPolicyError
from tracefork.metrics import extract_metrics
from tracefork.replay import ReplaySession
from tracefork.serialization import build_envelope, parse_envelope
from tracefork.storage import FilesystemFixtureStore
from tracefork.trajectory import build_graph
from tracefork_openai import instrument_openai

BASE_URL = os.environ.get("LIVE_SMOKE_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.environ.get("LIVE_SMOKE_MODEL", "openai/gpt-4o-mini")
PROMPT = "Reply with exactly one word: ok"
ONLY = [s for s in os.environ.get("LIVE_SMOKE_ONLY", "").split(",") if s]

CHECKS: list[tuple[str, bool, str]] = []
SKIPPED: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def skip(name: str, reason: str) -> None:
    SKIPPED.append(name)
    print(f"  [SKIP] {name} — {reason}")


def wanted(section: str) -> bool:
    return not ONLY or section in ONLY


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
    canonicalizer: Canonicalizer | None = None,
) -> tuple[BoundaryRuntime, BoundaryRegistry]:
    reg = registry if registry is not None else BoundaryRegistry()
    return BoundaryRuntime(registry=reg, canonicalizer=canonicalizer), reg


class LiveHarness:
    """Per-section bundle: fresh registry, record runtime, dead-transport replay."""

    def __init__(self, key: str, canonicalizer: Canonicalizer | None = None) -> None:
        self.key = key
        self.registry = BoundaryRegistry()
        self.runtime, _ = make_runtime(self.registry, canonicalizer)
        self.canonicalizer = canonicalizer
        self.dead = DeadTransport()
        self.recorder = make_client(key, httpx.AsyncHTTPTransport())
        instrument_openai(self.recorder, self.runtime)
        self.offline = make_client(key, self.dead)
        instrument_openai(self.offline, self.runtime)

    async def record(self, name: str, **kwargs: Any):
        with record(name) as rec:
            response = await self.recorder.responses.create(model=MODEL, **kwargs)
        return rec, response

    async def replay(self, fixture: Any, **kwargs: Any):
        session = ReplaySession(
            fixture=fixture, registry=self.registry, canonicalizer=self.canonicalizer
        )
        with session:
            response = await self.offline.responses.create(model=MODEL, **kwargs)
        return session, response

    def no_network(self) -> bool:
        return len(self.dead.requests) == 0


async def sec_record(key: str) -> Any:
    if not wanted("record"):
        return None, None, None
    print("\n== [1] record & metadata / hygiene / hermetic replay / persistence ==")
    harness = LiveHarness(key)
    rec, response = await harness.record("live-smoke", input=PROMPT, max_output_tokens=512)
    fixture = build_envelope(rec.trace)

    (invocation,) = rec.trace.invocations
    usage = invocation.metadata.get("usage", {})
    check("recorded a real SDK Response", bool(response.id), response.id)
    check("boundary name is the requested model", invocation.name == MODEL, invocation.name)
    check("output text is real model output", len(response.output_text) > 0)
    check("usage metadata captured", usage.get("input_tokens", 0) > 0, json.dumps(usage))
    check("latency captured", invocation.metadata.get("latency_ms", 0) >= 0)
    fingerprint = invocation.fingerprint
    check("request fingerprint stored", fingerprint is not None and len(fingerprint) == 64)

    fixture_text = fixture.to_json_bytes().decode("utf-8")
    check("API key never reaches fixture bytes", key not in fixture_text)

    session, replayed = await harness.replay(fixture, input=PROMPT, max_output_tokens=512)
    check("replay made zero network requests", harness.no_network())
    check("replay matched the recording", session.result.matched == 1)
    check("replayed id matches recording", replayed.id == response.id, replayed.id)
    check("replayed output matches recording", replayed.output_text == response.output_text)
    check("replay reports hermetic", session.result.is_hermetic)
    (candidate_invocation,) = session.result.trace.invocations
    check(
        "candidate invocation preserves recorded request",
        candidate_invocation.request == invocation.request,
    )
    check(
        "candidate invocation preserves recorded usage",
        candidate_invocation.metadata.get("usage") == usage,
    )

    with tempfile.TemporaryDirectory() as tmp:
        store = FilesystemFixtureStore(tmp)
        store.save("live-smoke", fixture)
        loaded = store.load("live-smoke")
        check("fixture store round trip preserves payload", loaded == fixture)
        _session_b, replayed_b = await harness.replay(loaded, input=PROMPT, max_output_tokens=512)
        _session_c, replayed_c = await harness.replay(loaded, input=PROMPT, max_output_tokens=512)
        check(
            "replay twice is deterministic",
            replayed_b.output_text == replayed_c.output_text == response.output_text,
        )
        check("store round trip made zero network requests", harness.no_network())

    from tracefork.errors import ReplayMismatchError

    session2 = ReplaySession(fixture=fixture, registry=harness.registry)
    mismatch_failed_closed = False
    with session2:
        try:
            await harness.offline.responses.create(
                model=MODEL, input="a completely different prompt", max_output_tokens=512
            )
        except ReplayMismatchError:
            mismatch_failed_closed = True
    check(
        "changed prompt fails closed (no live fallback)",
        mismatch_failed_closed and harness.no_network(),
    )
    return fixture, harness, response


async def sec_selective(key: str, harness: LiveHarness, fixture: Any) -> None:
    if not wanted("selective"):
        return
    print("\n== [2] selective replay: live LLM against the real service ==")
    live_client = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(live_client, harness.runtime)
    policy = ReplayPolicy(llm=ReplayMode.LIVE)
    session = ReplaySession(fixture=fixture, registry=harness.registry, policy=policy)
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


async def sec_cross(key: str) -> None:
    if not wanted("cross"):
        return None, None
    print("\n== [3] identical calls: same fingerprint, cross-replay ==")
    harness = LiveHarness(key)
    with record("run-1") as rec1:
        r1 = await harness.recorder.responses.create(
            model=MODEL, input=PROMPT, max_output_tokens=256
        )
    (inv1,) = rec1.trace.invocations
    with record("run-2") as rec2:
        r2 = await harness.recorder.responses.create(
            model=MODEL, input=PROMPT, max_output_tokens=256
        )
    (inv2,) = rec2.trace.invocations

    check("identical requests produce identical fingerprints", inv1.fingerprint == inv2.fingerprint)
    print(f"    run-1 output: {r1.output_text!r}")
    print(f"    run-2 output: {r2.output_text!r}")

    session = ReplaySession(fixture=build_envelope(rec1.trace), registry=harness.registry)
    with session:
        replayed = await harness.offline.responses.create(
            model=MODEL, input=PROMPT, max_output_tokens=256
        )
    check(
        "run-2 request served from run-1 recording",
        session.result.matched == 1 and harness.no_network(),
    )
    check("cross-replay returns run-1's response", replayed.output_text == r1.output_text)
    return rec1.trace, rec2.trace


async def sec_errors(key: str) -> None:
    if not wanted("errors"):
        return
    print("\n== [4] real API error taxonomy ==")
    from openai import APIStatusError

    cases = {
        "invalid model": {"model": "definitely/not-a-real-model-xyz", "input": "hi"},
        "empty input": {"model": MODEL, "input": ""},
        "negative max tokens": {"model": MODEL, "input": "hi", "max_output_tokens": -5},
    }
    for label, kwargs in cases.items():
        harness = LiveHarness(key)
        raised: str | None = None
        accepted: Any = None
        try:
            with record(f"error-{label}") as rec:
                accepted = await harness.recorder.responses.create(**kwargs)
        except APIStatusError as exc:
            raised = type(exc).__name__
            rec_status = rec.trace.status.value
            check(f"[{label}] real API error re-raised as SDK exception", True, raised)
            check(f"[{label}] trace marked failed", rec_status == "failed", rec_status)
            (invocation,) = rec.trace.invocations
            error = invocation.metadata.get("error", {})
            check(
                f"[{label}] error metadata captured",
                error.get("type") == raised,
                json.dumps(error)[:160],
            )
            fixture_text = build_envelope(rec.trace).to_json_bytes().decode("utf-8")
            check(f"[{label}] error fixture key-free", key not in fixture_text)
        if raised is None:
            # Provider accepted the request; nothing to assert about errors.
            skip(f"[{label}] error capture", f"provider accepted the request: {accepted!r:.80}")


async def sec_unicode(key: str) -> None:
    if not wanted("unicode"):
        return
    print("\n== [5] unicode round trip ==")
    harness = LiveHarness(key)
    prompt = "Reply with exactly: 🌊 你好 café"
    rec, response = await harness.record("unicode-case", input=prompt, max_output_tokens=512)
    fixture = build_envelope(rec.trace)
    text = fixture.to_json_bytes().decode("utf-8")
    check("unicode survives canonical serialization", "🌊" in text or "你好" in text)
    _session, replayed = await harness.replay(fixture, input=prompt, max_output_tokens=512)
    check("unicode replay identical", replayed.output_text == response.output_text)
    check("unicode replay made zero requests", harness.no_network())


async def sec_parallel(key: str) -> None:
    if not wanted("parallel"):
        return
    print("\n== [6] parallel live calls + parallel hermetic replay ==")
    harness = LiveHarness(key)

    async def one(n: int) -> str:
        r = await harness.recorder.responses.create(
            model=MODEL,
            input=f"Reply with exactly one word, any word ({n}).",
            max_output_tokens=256,
        )
        return r.output_text

    with record("parallel-live") as rec:
        outputs = await asyncio.gather(one(1), one(2), one(3))
    check("parallel live calls all recorded", len(rec.trace.invocations) == 3, str(outputs))

    async def replay_one(n: int) -> str:
        r = await harness.offline.responses.create(
            model=MODEL,
            input=f"Reply with exactly one word, any word ({n}).",
            max_output_tokens=256,
        )
        return r.output_text

    session = ReplaySession(fixture=build_envelope(rec.trace), registry=harness.registry)
    with session:
        replayed_outputs = await asyncio.gather(replay_one(1), replay_one(2), replay_one(3))
    check("parallel hermetic replay matches", replayed_outputs == list(outputs))
    check("parallel replay made zero requests", harness.no_network())
    check("parallel replay matched all three", session.result.matched == 3)


async def sec_tool_loop(key: str) -> None:
    if not wanted("toolloop"):
        return
    print("\n== [7] agentic tool-call loop (LLM -> tool -> LLM) ==")
    harness = LiveHarness(key)
    toolbox = ToolBox(harness.runtime)

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

    from openai import APIStatusError

    rec = None
    tool_choice_used = "required"
    try:
        with record("agent-loop") as rec:
            live_r1, live_r2 = await agent_loop(harness.recorder, tool_choice="required")
    except (LookupError, APIStatusError):
        tool_choice_used = "auto"
        try:
            with record("agent-loop") as rec:
                live_r1, live_r2 = await agent_loop(harness.recorder, tool_choice="auto")
        except (LookupError, APIStatusError) as exc:
            skip("agentic tool-call loop", f"provider/model did not cooperate: {exc}")
            return

    check("loop used the tool", len(rec.trace.invocations) == 3, "expected 2 llm + 1 tool")
    families = [inv.boundary_type.split(".", 1)[0] for inv in rec.trace.invocations]
    check(
        "loop trajectory is llm -> tool -> llm", families == ["llm", "tool", "llm"], str(families)
    )

    session = ReplaySession(fixture=build_envelope(rec.trace), registry=harness.registry)
    with session:
        replay_r1, replay_r2 = await agent_loop(harness.offline, tool_choice_used)
    check("full agent loop replayed with zero network", harness.no_network())
    check("all three boundaries matched", session.result.matched == 3)
    check("replayed final answer identical", replay_r2 == live_r2)
    check("replayed first response identical", replay_r1 == live_r1)
    check("loop replay reports hermetic", session.result.is_hermetic)


async def sec_httpx(key: str) -> None:
    if not wanted("httpx"):
        return
    print("\n== [8] httpx adapter: raw chat.completions record/replay ==")
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


async def sec_streaming(key: str) -> None:
    if not wanted("streaming"):
        return
    print("\n== [9] streaming deep dive ==")
    harness = LiveHarness(key)

    rec, _ = await harness.record(
        "stream-long",
        input="Count from 1 to 10, one number per line.",
        max_output_tokens=1024,
        stream=True,
    )
    (invocation,) = rec.trace.invocations
    events = invocation.response.get("events", [])
    deltas = [e for e in events if e.get("type") == "response.output_text.delta"]
    completed = [e for e in events if e.get("type") == "response.completed"]
    check(
        "stream captured delta and completion events",
        len(deltas) >= 1 and len(completed) == 1,
        f"{len(deltas)} deltas",
    )
    check("stream completion event captured", len(completed) == 1)
    check(
        "stream usage captured in metadata",
        invocation.metadata.get("usage", {}).get("input_tokens", 0) > 0,
    )

    fixture = build_envelope(rec.trace)
    session = ReplaySession(fixture=fixture, registry=harness.registry)
    with session:
        replayed_stream = await harness.offline.responses.create(
            model=MODEL,
            input="Count from 1 to 10, one number per line.",
            max_output_tokens=1024,
            stream=True,
        )
        replayed_types = [event.type async for event in replayed_stream]
    check(
        "stream replay preserves event sequence",
        replayed_types == [e.get("type") for e in events],
        f"{len(events)} -> {len(replayed_types)}",
    )
    check("stream replay made zero requests", harness.no_network())

    session2 = ReplaySession(fixture=fixture, registry=harness.registry)
    stream_mismatch_failed = False
    with session2:
        try:
            await harness.offline.responses.create(
                model=MODEL, input="completely different", max_output_tokens=1024, stream=True
            )
        except ReplayMismatchError:
            stream_mismatch_failed = True
    check("stream mismatch fails closed", stream_mismatch_failed and harness.no_network())


async def sec_params(key: str) -> None:
    if not wanted("params"):
        return
    print("\n== [10] parameter matrix ==")
    cases: dict[str, dict[str, Any]] = {
        "temperature-zero": {"temperature": 0.0},
        "sampling": {"temperature": 0.7, "top_p": 0.9},
        "metadata": {"metadata": {"tracefork": "live-smoke"}},
        "instructions": {"instructions": "You are a terse assistant."},
    }
    from openai import APIStatusError

    for case_name, extra in cases.items():
        harness = LiveHarness(key)
        try:
            rec, response = await harness.record(
                f"param-{case_name}", input=PROMPT, max_output_tokens=256, **extra
            )
        except APIStatusError as exc:
            skip(f"param {case_name}", f"provider rejected parameters: {str(exc)[:100]}")
            continue
        fixture = build_envelope(rec.trace)
        check(f"param {case_name}: recorded", bool(response.id))
        _session, replayed = await harness.replay(
            fixture, input=PROMPT, max_output_tokens=256, **extra
        )
        check(f"param {case_name}: replay identical", replayed.output_text == response.output_text)
        check(f"param {case_name}: zero network", harness.no_network())


async def sec_payloads(key: str) -> None:
    if not wanted("payloads"):
        return
    print("\n== [11] long output and JSON output payloads ==")
    harness = LiveHarness(key)

    rec, response = await harness.record(
        "long-output",
        input="Write three full sentences about the sea.",
        max_output_tokens=1024,
    )
    fixture = build_envelope(rec.trace)
    usage = rec.trace.invocations[0].metadata.get("usage", {})
    check(
        "long output recorded",
        len(response.output_text) > 0,
        f"{usage.get('output_tokens')} tokens",
    )
    _session, replayed = await harness.replay(
        fixture, input="Write three full sentences about the sea.", max_output_tokens=1024
    )
    check("long output replay identical", replayed.output_text == response.output_text)
    check("long output replay zero network", harness.no_network())

    harness2 = LiveHarness(key)
    rec2, response2 = await harness2.record(
        "json-output",
        input='Return a JSON object exactly like {"ok": true, "n": 1}.',
        max_output_tokens=512,
    )
    check("json output recorded", len(response2.output_text) > 0)
    _session2, replayed2 = await harness2.replay(
        build_envelope(rec2.trace),
        input='Return a JSON object exactly like {"ok": true, "n": 1}.',
        max_output_tokens=512,
    )
    check("json output replay identical", replayed2.output_text == response2.output_text)


async def sec_matching(key: str) -> None:
    if not wanted("matching"):
        return
    print("\n== [12] matching precision ==")
    harness = LiveHarness(key)
    rec, _ = await harness.record("match-case", input=PROMPT, max_output_tokens=256)
    fixture = build_envelope(rec.trace)

    # Model change: boundary name differs, fingerprint differs.
    from tracefork.errors import ReplayMismatchError

    session = ReplaySession(fixture=fixture, registry=harness.registry)
    model_mismatch = False
    with session:
        try:
            await harness.offline.responses.create(
                model="openai/gpt-4o-mini", input=PROMPT, max_output_tokens=256
            )
        except ReplayMismatchError:
            model_mismatch = True
    check("model change fails closed", model_mismatch and harness.no_network())

    # Whitespace-only input change: canonical JSON preserves string content.
    session2 = ReplaySession(fixture=fixture, registry=harness.registry)
    whitespace_mismatch = False
    with session2:
        try:
            await harness.offline.responses.create(
                model=MODEL, input=PROMPT + " ", max_output_tokens=256
            )
        except ReplayMismatchError:
            whitespace_mismatch = True
    check("whitespace-only change fails closed", whitespace_mismatch and harness.no_network())

    # Occurrence exhaustion: one recording, two identical replays.
    session3, _ = await harness.replay(fixture, input=PROMPT, max_output_tokens=256)
    exhausted = False
    with session3:
        try:
            await harness.offline.responses.create(model=MODEL, input=PROMPT, max_output_tokens=256)
        except ReplayMismatchError:
            exhausted = True
    check(
        "occurrence exhaustion fails closed",
        exhausted and session3.result.matched == 1 and harness.no_network(),
    )


async def sec_ignore_rules(key: str) -> None:
    if not wanted("ignore"):
        return
    print("\n== [13] ignore rules with a genuinely volatile request field ==")
    from uuid import uuid4

    from openai import APIStatusError

    rules = ["metadata.request_id"]
    record_harness = LiveHarness(key, canonicalizer=Canonicalizer(ignore=rules))
    plain_harness = LiveHarness(key)

    request_id_a = uuid4().hex
    request_id_b = uuid4().hex
    response_a = None
    try:
        rec_a, response_a = await record_harness.record(
            "ignore-a", input=PROMPT, max_output_tokens=64, metadata={"request_id": request_id_a}
        )
        rec_b, _ = await record_harness.record(
            "ignore-b", input=PROMPT, max_output_tokens=64, metadata={"request_id": request_id_b}
        )
        rec_plain_a, _ = await plain_harness.record(
            "ignore-plain-a",
            input=PROMPT,
            max_output_tokens=64,
            metadata={"request_id": request_id_a},
        )
        rec_plain_b, _ = await plain_harness.record(
            "ignore-plain-b",
            input=PROMPT,
            max_output_tokens=64,
            metadata={"request_id": request_id_b},
        )
    except APIStatusError as exc:
        skip("ignore rules", f"provider rejected metadata field: {str(exc)[:100]}")
        return

    fp_ignored_a = rec_a.trace.invocations[0].fingerprint
    fp_ignored_b = rec_b.trace.invocations[0].fingerprint
    fp_plain_a = rec_plain_a.trace.invocations[0].fingerprint
    fp_plain_b = rec_plain_b.trace.invocations[0].fingerprint

    check(
        "volatile field changes fingerprints without ignore rules",
        fp_plain_a != fp_plain_b,
    )
    check(
        "ignore rules make volatile-field fingerprints equal",
        fp_ignored_a == fp_ignored_b,
    )

    # Replay call B against recording A under the ignore rules: must match.
    dead = DeadTransport()
    offline = make_client(key, dead)
    runtime, _ = make_runtime(record_harness.registry, canonicalizer=Canonicalizer(ignore=rules))
    instrument_openai(offline, runtime)
    session = ReplaySession(
        fixture=build_envelope(rec_a.trace),
        registry=record_harness.registry,
        canonicalizer=Canonicalizer(ignore=rules),
    )
    with session:
        replayed = await offline.responses.create(
            model=MODEL,
            input=PROMPT,
            max_output_tokens=64,
            metadata={"request_id": request_id_b},
        )
    check("replay across different request_ids matches", session.result.matched == 1)
    check("ignore-rule replay made zero requests", len(dead.requests) == 0)
    check(
        "cross-request_id replay returns recording A's output",
        replayed.output_text == response_a.output_text,
    )


async def sec_chained(key: str) -> None:
    if not wanted("chained"):
        return
    print("\n== [14] chained conversation: replayed output feeds the next input ==")
    harness = LiveHarness(key)
    turn1 = "Remember the codeword: BANANA42."
    with record("chain") as rec:
        r1 = await harness.recorder.responses.create(
            model=MODEL, input=turn1, max_output_tokens=512
        )
        input2 = [
            {"role": "user", "content": turn1},
            {"role": "assistant", "content": r1.output_text},
            {"role": "user", "content": "What was the codeword? Reply with it only."},
        ]
        r2 = await harness.recorder.responses.create(
            model=MODEL, input=input2, max_output_tokens=512
        )
    check("chained conversation recorded", len(rec.trace.invocations) == 2)

    session = ReplaySession(fixture=build_envelope(rec.trace), registry=harness.registry)
    with session:
        replay_r1 = await harness.offline.responses.create(
            model=MODEL, input=turn1, max_output_tokens=512
        )
        replay_input2 = [
            {"role": "user", "content": turn1},
            {"role": "assistant", "content": replay_r1.output_text},
            {"role": "user", "content": "What was the codeword? Reply with it only."},
        ]
        replay_r2 = await harness.offline.responses.create(
            model=MODEL, input=replay_input2, max_output_tokens=512
        )
    check("both chained calls matched from recordings", session.result.matched == 2)
    check("chained replay made zero network requests", harness.no_network())
    check("chained final answer identical", replay_r2.output_text == r2.output_text)


async def sec_two_tools(key: str) -> None:
    if not wanted("twotools"):
        return
    print("\n== [15] two-tool branch selection ==")
    harness = LiveHarness(key)
    toolbox = ToolBox(harness.runtime)

    @toolbox.tool(name="get_weather")
    async def get_weather(city: str) -> dict[str, Any]:
        return {"temperature": 21, "city": city}

    @toolbox.tool(name="get_time")
    async def get_time(city: str) -> dict[str, Any]:
        return {"time": "12:00", "city": city}

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
        },
        {
            "type": "function",
            "name": "get_time",
            "description": "Get the current time for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    ]

    async def agent_loop(client: AsyncOpenAI) -> tuple[str, str, str]:
        r1 = await client.responses.create(
            model=MODEL,
            input="What time is it in Berlin? Use the get_time tool.",
            tools=tools,
            max_output_tokens=1024,
        )
        call = next((o for o in r1.output if getattr(o, "type", "") == "function_call"), None)
        if call is None:
            raise LookupError("model did not emit a function call")
        chosen = call.name
        if chosen == "get_time":
            result = await get_time(**json.loads(call.arguments))
        elif chosen == "get_weather":
            result = await get_weather(**json.loads(call.arguments))
        else:
            raise LookupError(f"unknown tool {chosen}")
        r2 = await client.responses.create(
            model=MODEL,
            input=[
                {"role": "user", "content": "What time is it in Berlin? Use the get_time tool."},
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
        return chosen, r1.output_text, r2.output_text

    from openai import APIStatusError

    recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(recorder, harness.runtime)
    rec = None
    try:
        with record("two-tools") as rec:
            chosen, _live_r1, live_r2 = await agent_loop(recorder)
    except (LookupError, APIStatusError) as exc:
        skip("two-tool branch", f"provider/model did not cooperate: {exc}")
        return

    check("branch recorded exactly three boundaries", len(rec.trace.invocations) == 3)
    check("chosen tool is one of the two offered", chosen in {"get_weather", "get_time"}, chosen)
    session = ReplaySession(fixture=build_envelope(rec.trace), registry=harness.registry)
    with session:
        _, _, replay_r2 = await agent_loop(harness.offline)
    check("branch replay matched all boundaries", session.result.matched == 3)
    check("branch replay made zero requests", harness.no_network())
    check("branch replay final answer identical", replay_r2 == live_r2)


async def sec_real_data(key: str) -> None:
    if not wanted("realdata"):
        return
    print("\n== [16] metrics, assertions and diff over real traces ==")
    harness = LiveHarness(key)
    rec, _ = await harness.record("metrics-case", input=PROMPT, max_output_tokens=512)
    trace = rec.trace
    fixture = build_envelope(trace)

    metrics = extract_metrics(trace)
    (invocation,) = trace.invocations
    usage = invocation.metadata.get("usage", {})
    check(
        "metrics token counts match live usage", metrics.input_tokens == usage.get("input_tokens")
    )
    check("metrics llm call count matches", metrics.llm_calls == 1)
    check("wall clock measured", (metrics.wall_clock_seconds or 0) > 0)

    passing = TraceExpectations(max=ResourceMaximums(total_tokens=(metrics.total_tokens or 0) * 10))
    results_pass = evaluate_expectations(fixture.trace, passing)
    check("generous token assertion passes", all(r.passed for r in results_pass))
    strict = TraceExpectations(max=ResourceMaximums(total_tokens=1))
    results_strict = evaluate_expectations(fixture.trace, strict)
    check(
        "strict token assertion fails with evidence",
        any(not r.passed for r in results_strict),
    )

    # Same trajectory twice: diff is clean regardless of output differences.
    harness2 = LiveHarness(key)
    rec2, _ = await harness2.record("metrics-case", input=PROMPT, max_output_tokens=512)
    result = diff_traces(fixture.trace, build_envelope(rec2.trace).trace)
    check("identical-trajectory diff has no divergence", result.first_divergence is None)
    check(
        "diff aligned every node",
        all(op.op == "match" for op in result.ops) and len(result.ops) == len(trace.spans),
    )
    tokens_delta = next(d for d in result.resources if d.name == "tokens")
    check(
        "diff reports real token deltas",
        tokens_delta.baseline is not None and tokens_delta.candidate is not None,
    )

    graph = build_graph(fixture.trace)
    check("real fixture builds a valid graph", len(graph.nodes) == len(trace.spans))


async def sec_policies(key: str) -> None:
    if not wanted("policies"):
        return
    print("\n== [17] policy edges ==")
    harness = LiveHarness(key)
    rec, _ = await harness.record("policy-case", input=PROMPT, max_output_tokens=256)
    fixture = build_envelope(rec.trace)

    live_client = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(live_client, harness.runtime)
    policy = ReplayPolicy(default=ReplayMode.LIVE)
    session = ReplaySession(fixture=fixture, registry=harness.registry, policy=policy)
    with session:
        live_response = await live_client.responses.create(
            model=MODEL, input=PROMPT, max_output_tokens=256
        )
    check(
        "default-LIVE policy runs the real API",
        bool(live_response.id) and harness.no_network(),
        live_response.id,
    )
    check("default-LIVE matches nothing", session.result.matched == 0)
    check(
        "default-LIVE live boundary named",
        session.result.live_boundaries == [f"llm.openai.{MODEL}"],
        str(session.result.live_boundaries),
    )

    mock_policy = ReplayPolicy(default=ReplayMode.MOCK)
    session2 = ReplaySession(fixture=fixture, registry=harness.registry, policy=mock_policy)
    mock_failed = False
    with session2:
        try:
            await harness.offline.responses.create(model=MODEL, input=PROMPT, max_output_tokens=256)
        except ReplayPolicyError:
            mock_failed = True
    check("MOCK mode fails closed (not implemented)", mock_failed and harness.no_network())


async def sec_integrity(key: str) -> None:
    if not wanted("integrity"):
        return
    print("\n== [18] fixture integrity tampering on real payloads ==")
    harness = LiveHarness(key)
    rec, _ = await harness.record("integrity-case", input=PROMPT, max_output_tokens=256)
    fixture = build_envelope(rec.trace)
    original_bytes = fixture.to_json_bytes()

    # 1. Content tamper (valid JSON, different payload) detected by the digest.
    tampered = original_bytes.replace(b'"integrity-case"', b'"integrity-tamper"', 1)
    try:
        parse_envelope(tampered)
        check("content tamper detected by digest", False, "tampered fixture parsed")
    except Exception as exc:
        check(
            "content tamper detected by digest",
            "digest mismatch" in str(exc),
            str(exc)[:80],
        )

    # 2. Future schema version rejected.
    mutated = json.loads(original_bytes)
    mutated["trace"]["schema_version"] = "99.0"
    from tracefork.models import Trace

    resealed = build_envelope(Trace.model_validate(mutated["trace"]))
    try:
        parse_envelope(resealed.to_json_bytes())
        check("future schema version rejected", False, "parsed")
    except Exception as exc:
        check("future schema version rejected", "schema_version" in str(exc), str(exc)[:80])

    # 3. Cycle injected into a real trace rejected at load.
    spans = [span_item.model_copy() for span_item in fixture.trace.spans]
    for span_item in spans:
        span_item.parent_span_id = spans[-1].span_id
    spans[-1].parent_span_id = spans[0].span_id
    cycled = build_envelope(fixture.trace.model_copy(update={"spans": spans}))
    try:
        parse_envelope(cycled.to_json_bytes())
        check("cycled real trace rejected", False, "parsed")
    except Exception as exc:
        check(
            "cycled real trace rejected",
            "cycle" in str(exc),
            str(exc)[:80],
        )

    # 4. Untampered real fixture parses and builds a valid graph.
    parsed = parse_envelope(original_bytes)
    graph = build_graph(parsed.trace)
    check("untampered real fixture valid", len(graph.nodes) == len(fixture.trace.spans))


async def main() -> int:
    key = os.environ.get("LIVE_SMOKE_API_KEY")
    if not key:
        print("LIVE_SMOKE_API_KEY not set; skipping live smoke (exit 3)")
        return 3

    fixture, harness, _ = await sec_record(key)
    await sec_selective(key, harness, fixture)
    await sec_cross(key)
    await sec_errors(key)
    await sec_unicode(key)
    await sec_parallel(key)
    await sec_tool_loop(key)
    await sec_httpx(key)
    await sec_streaming(key)
    await sec_params(key)
    await sec_payloads(key)
    await sec_matching(key)
    await sec_ignore_rules(key)
    await sec_chained(key)
    await sec_two_tools(key)
    await sec_real_data(key)
    await sec_policies(key)
    await sec_integrity(key)

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
