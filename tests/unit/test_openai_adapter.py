"""TF-070..074: OpenAI adapter — sync/async interception, metadata, replay, streaming.

The OpenAI SDK runs against an in-process mock transport, so tests exercise the
real SDK parsing path. Replay uses a transport that fails the test if the
server is contacted — proving hermetic behavior.
"""

import json
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI, OpenAI
from openai.types.responses import Response
from tracefork import record
from tracefork.adapters.openai import instrument_openai
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime, ReplayMode, ReplayPolicy
from tracefork.errors import ReplayMismatchError
from tracefork.replay import ReplaySession
from tracefork.serialization import FixtureEnvelope, build_envelope

RESPONSE_PAYLOAD: dict[str, Any] = {
    "id": "resp_123",
    "object": "response",
    "created_at": 1756818000,
    "status": "completed",
    "model": "gpt-test",
    "output": [
        {
            "type": "message",
            "id": "msg_1",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hello!", "annotations": []}],
        }
    ],
    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
}

SSE_EVENTS: list[dict[str, Any]] = [
    {
        "type": "response.output_text.delta",
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "delta": "Hel",
        "logprobs": None,
        "sequence_number": 1,
        "obfuscation": None,
    },
    {
        "type": "response.output_text.delta",
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "delta": "lo",
        "logprobs": None,
        "sequence_number": 2,
        "obfuscation": None,
    },
    {
        "type": "response.completed",
        "sequence_number": 3,
        "response": {
            "id": "resp_s",
            "object": "response",
            "created_at": 1756818000,
            "status": "completed",
            "model": "gpt-test",
            "output": [],
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        },
    },
]


def json_transport(payload: dict[str, Any], calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def sse_transport(events: list[dict[str, Any]], calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    return httpx.MockTransport(handler)


def dead_transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("network must not be touched during replay")

    return httpx.MockTransport(handler)


async def test_record_async_response_preserves_sdk_shape() -> None:
    calls: list[httpx.Request] = []
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=json_transport(RESPONSE_PAYLOAD, calls)),
    )
    instrument_openai(client, runtime)

    with record("case") as rec:
        response = await client.responses.create(model="gpt-test", input="hi")

    assert isinstance(response, Response)
    assert response.output_text == "Hello!"
    assert len(calls) == 1

    (invocation,) = rec.trace.invocations
    assert invocation.boundary_type == "llm.openai"
    assert invocation.name == "gpt-test"
    assert invocation.request == {"model": "gpt-test", "input": "hi"}
    assert invocation.response["id"] == "resp_123"
    assert invocation.metadata["usage"]["input_tokens"] == 10
    assert invocation.metadata["usage"]["output_tokens"] == 5
    assert invocation.metadata["finish_reason"] == "completed"
    assert "latency_ms" in invocation.metadata

    (span,) = rec.trace.spans
    assert span.kind.value == "llm"
    assert span.name == "gpt-test"


async def test_record_never_persists_api_key() -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    calls: list[httpx.Request] = []
    client = AsyncOpenAI(
        api_key="super-secret-key",
        http_client=httpx.AsyncClient(transport=json_transport(RESPONSE_PAYLOAD, calls)),
    )
    instrument_openai(client, runtime)

    with record("case") as rec:
        await client.responses.create(model="gpt-test", input="hi")
    fixture = build_envelope(rec.trace)

    assert "super-secret-key" not in fixture.to_json_bytes().decode("utf-8")


async def test_replay_async_offline_restores_sdk_object() -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    record_calls: list[httpx.Request] = []
    client = AsyncOpenAI(
        api_key="k",
        http_client=httpx.AsyncClient(transport=json_transport(RESPONSE_PAYLOAD, record_calls)),
    )
    instrument_openai(client, runtime)

    with record("case") as rec:
        await client.responses.create(model="gpt-test", input="hi")
    fixture = build_envelope(rec.trace)
    assert len(record_calls) == 1

    # The "server is down": any request fails the test via dead_transport.
    replay_calls: list[httpx.Request] = []
    offline = AsyncOpenAI(
        api_key="k", http_client=httpx.AsyncClient(transport=dead_transport(replay_calls))
    )
    instrument_openai(offline, runtime)

    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        response = await offline.responses.create(model="gpt-test", input="hi")

    assert isinstance(response, Response)
    assert response.id == "resp_123"
    assert response.output_text == "Hello!"
    assert replay_calls == []  # hermetic
    assert session.result.matched == 1


async def test_live_llm_policy_performs_real_call() -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    record_calls: list[httpx.Request] = []
    client = AsyncOpenAI(
        api_key="k",
        http_client=httpx.AsyncClient(transport=json_transport(RESPONSE_PAYLOAD, record_calls)),
    )
    instrument_openai(client, runtime)
    with record("case") as rec:
        await client.responses.create(model="gpt-test", input="hi")
    fixture = build_envelope(rec.trace)

    live_calls: list[httpx.Request] = []
    live = AsyncOpenAI(
        api_key="k",
        http_client=httpx.AsyncClient(transport=json_transport(RESPONSE_PAYLOAD, live_calls)),
    )
    instrument_openai(live, runtime)
    session = ReplaySession(
        fixture=fixture, registry=registry, policy=ReplayPolicy(llm=ReplayMode.LIVE)
    )
    with session:
        response = await live.responses.create(model="gpt-test", input="hi")

    assert response.id == "resp_123"
    assert len(live_calls) == 1  # the real call happened
    assert session.result.live_boundaries == ["llm.openai.gpt-test"]


def test_sync_client_record_and_replay() -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    record_calls: list[httpx.Request] = []
    client = OpenAI(
        api_key="k",
        http_client=httpx.Client(transport=json_transport(RESPONSE_PAYLOAD, record_calls)),
    )
    instrument_openai(client, runtime)

    with record("case") as rec:
        response = client.responses.create(model="gpt-test", input="hi")
    assert isinstance(response, Response)
    assert response.output_text == "Hello!"
    assert len(record_calls) == 1

    fixture: FixtureEnvelope = build_envelope(rec.trace)
    replay_calls: list[httpx.Request] = []
    offline = OpenAI(api_key="k", http_client=httpx.Client(transport=dead_transport(replay_calls)))
    instrument_openai(offline, runtime)
    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        replayed = offline.responses.create(model="gpt-test", input="hi")

    assert replayed.id == "resp_123"
    assert replay_calls == []


async def test_streaming_record_and_replay() -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    record_calls: list[httpx.Request] = []
    client = AsyncOpenAI(
        api_key="k",
        http_client=httpx.AsyncClient(transport=sse_transport(SSE_EVENTS, record_calls)),
    )
    instrument_openai(client, runtime)

    with record("case") as rec:
        stream = await client.responses.create(model="gpt-test", input="hi", stream=True)
        deltas = [e.delta for e in stream if e.type == "response.output_text.delta"]
    assert deltas == ["Hel", "lo"]
    assert len(record_calls) == 1

    (invocation,) = rec.trace.invocations
    assert invocation.metadata["stream"] is True
    assert [e["type"] for e in invocation.response["events"]] == [
        "response.output_text.delta",
        "response.output_text.delta",
        "response.completed",
    ]

    fixture = build_envelope(rec.trace)
    replay_calls: list[httpx.Request] = []
    offline = AsyncOpenAI(
        api_key="k", http_client=httpx.AsyncClient(transport=dead_transport(replay_calls))
    )
    instrument_openai(offline, runtime)
    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        replayed = await offline.responses.create(model="gpt-test", input="hi", stream=True)
        replayed_deltas = [e.delta for e in replayed if e.type == "response.output_text.delta"]

    assert replayed_deltas == ["Hel", "lo"]
    assert replay_calls == []


def test_sync_streaming_record_and_replay() -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    record_calls: list[httpx.Request] = []
    client = OpenAI(
        api_key="k", http_client=httpx.Client(transport=sse_transport(SSE_EVENTS, record_calls))
    )
    instrument_openai(client, runtime)

    with record("case") as rec:
        stream = client.responses.create(model="gpt-test", input="hi", stream=True)
        deltas = [e.delta for e in stream if e.type == "response.output_text.delta"]
    assert deltas == ["Hel", "lo"]

    fixture = build_envelope(rec.trace)
    replay_calls: list[httpx.Request] = []
    offline = OpenAI(api_key="k", http_client=httpx.Client(transport=dead_transport(replay_calls)))
    instrument_openai(offline, runtime)
    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        replayed = offline.responses.create(model="gpt-test", input="hi", stream=True)
        replayed_deltas = [e.delta for e in replayed if e.type == "response.output_text.delta"]
    assert replayed_deltas == ["Hel", "lo"]
    assert replay_calls == []


async def test_replay_mismatch_fails_closed_with_diagnostics() -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    record_calls: list[httpx.Request] = []
    client = AsyncOpenAI(
        api_key="k",
        http_client=httpx.AsyncClient(transport=json_transport(RESPONSE_PAYLOAD, record_calls)),
    )
    instrument_openai(client, runtime)
    with record("case") as rec:
        await client.responses.create(model="gpt-test", input="hi")
    fixture = build_envelope(rec.trace)

    replay_calls: list[httpx.Request] = []
    offline = AsyncOpenAI(
        api_key="k", http_client=httpx.AsyncClient(transport=dead_transport(replay_calls))
    )
    instrument_openai(offline, runtime)
    session = ReplaySession(fixture=fixture, registry=registry)
    with session, pytest.raises(ReplayMismatchError):
        await offline.responses.create(model="gpt-test", input="different question")
    assert replay_calls == []


async def test_response_without_usage_records_null_metadata() -> None:
    payload = {k: v for k, v in RESPONSE_PAYLOAD.items() if k != "usage"}
    calls: list[httpx.Request] = []
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = AsyncOpenAI(
        api_key="k", http_client=httpx.AsyncClient(transport=json_transport(payload, calls))
    )
    instrument_openai(client, runtime)

    with record("case") as rec:
        await client.responses.create(model="gpt-test", input="hi")

    (invocation,) = rec.trace.invocations
    assert invocation.metadata["usage"] == {}
    assert invocation.metadata["finish_reason"] == "completed"
