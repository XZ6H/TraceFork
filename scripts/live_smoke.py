"""Live smoke test against a real OpenAI-compatible API (maintainer task).

Validates the OpenAI adapter against real service traffic:

1. record a real ``responses.create`` call (SDK objects, usage metadata),
2. prove the API key never reaches the fixture bytes,
3. replay hermetically with a transport that fails on any socket attempt,
4. record a real streaming call and replay its event sequence offline.

Usage (never commit keys):

    LIVE_SMOKE_API_KEY=sk-or-v1-... \
    LIVE_SMOKE_MODEL=meta/muse-spark-1.3-contributor \
    LIVE_SMOKE_BASE_URL=https://openrouter.ai/api/v1 \
        uv run python scripts/live_smoke.py

Exits 0 when every check passes; exits 1 with the failing check otherwise.
Exits 3 when LIVE_SMOKE_API_KEY is unset (skip in CI).
"""

import asyncio
import json
import os
import sys

import httpx
from openai import AsyncOpenAI
from tracefork import record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.replay import ReplaySession
from tracefork.serialization import build_envelope
from tracefork_openai import instrument_openai

BASE_URL = os.environ.get("LIVE_SMOKE_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.environ.get("LIVE_SMOKE_MODEL", "openai/gpt-4o-mini")
PROMPT = "Reply with exactly one word: ok"


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


async def main() -> int:
    key = os.environ.get("LIVE_SMOKE_API_KEY")
    if not key:
        print("LIVE_SMOKE_API_KEY not set; skipping live smoke (exit 3)")
        return 3

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, passed, detail))
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    registry = BoundaryRegistry()
    record_runtime = BoundaryRuntime(registry=registry)

    # ---- 1. Record one real call -------------------------------------
    recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(recorder, record_runtime)
    with record("live-smoke", input=PROMPT) as rec:
        response = await recorder.responses.create(model=MODEL, input=PROMPT, max_output_tokens=512)
    fixture = build_envelope(rec.trace)

    print(f"recorded: status={response.status} output={response.output_text!r}")

    (invocation,) = rec.trace.invocations
    usage = invocation.metadata.get("usage", {})
    check("recorded a real SDK Response", response.id.startswith(("resp", "gen")), response.id)
    check("output text is real model output", len(response.output_text) > 0)
    check("usage metadata captured", usage.get("input_tokens", 0) > 0, json.dumps(usage))
    check("latency captured", invocation.metadata.get("latency_ms", 0) >= 0)
    fingerprint = invocation.fingerprint
    check("request fingerprint stored", fingerprint is not None and len(fingerprint) == 64)

    # ---- 2. Key hygiene ------------------------------------------------
    fixture_text = fixture.to_json_bytes().decode("utf-8")
    check("API key never reaches fixture bytes", key not in fixture_text)

    # ---- 3. Hermetic replay -------------------------------------------
    dead = DeadTransport()
    offline = make_client(key, dead)
    instrument_openai(offline, record_runtime)
    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        replayed = await offline.responses.create(model=MODEL, input=PROMPT, max_output_tokens=512)
    check("replay made zero network requests", len(dead.requests) == 0)
    check("replay matched the recording", session.result.matched == 1)
    check("replayed id matches recording", replayed.id == response.id, replayed.id)
    check(
        "replayed output matches recording",
        replayed.output_text == response.output_text,
    )
    check("replay reports hermetic", session.result.is_hermetic)

    # ---- 4. Mismatch fails closed against the live service ------------
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

    # ---- 5. Streaming: record and replay offline -----------------------
    stream_recorder = make_client(key, httpx.AsyncHTTPTransport())
    instrument_openai(stream_recorder, record_runtime)
    with record("live-smoke-stream") as rec_stream:
        stream = await stream_recorder.responses.create(
            model=MODEL, input=PROMPT, max_output_tokens=512, stream=True
        )
        live_deltas = [
            event.delta
            async for event in stream
            if getattr(event, "type", "") == "response.output_text.delta"
        ]

    stream_fixture = build_envelope(rec_stream.trace)
    dead2 = DeadTransport()
    stream_offline = make_client(key, dead2)
    instrument_openai(stream_offline, record_runtime)
    stream_session = ReplaySession(fixture=stream_fixture, registry=registry)
    with stream_session:
        replayed_stream = await stream_offline.responses.create(
            model=MODEL, input=PROMPT, max_output_tokens=512, stream=True
        )
        replayed_deltas = [
            event.delta
            async for event in replayed_stream
            if getattr(event, "type", "") == "response.output_text.delta"
        ]
    check("streamed real deltas", len(live_deltas) > 0, f"{len(live_deltas)} delta events")
    check("replayed stream matches recording", replayed_deltas == live_deltas)
    check("stream replay made zero network requests", len(dead2.requests) == 0)

    print()
    failed = [name for name, passed, _ in checks if not passed]
    for name, _, detail in checks:
        if name in failed:
            print(f"FAILED: {name} {detail}")
    print(f"{len(checks) - len(failed)}/{len(checks)} live smoke checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
