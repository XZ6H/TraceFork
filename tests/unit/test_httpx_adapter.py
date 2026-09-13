"""TF-080..082: httpx adapter — interception, secret-safe defaults, hermetic replay."""

import json
from typing import Any

import httpx
import pytest
from tracefork import record
from tracefork.adapters.httpx import TraceForkAsyncTransport
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.serialization import build_envelope

JSON_PAYLOAD: dict[str, Any] = {"orders": [{"id": 1, "total": 149.0}]}


def real_transport(calls: list[httpx.Request], body: bytes | None = None) -> httpx.MockTransport:
    """Stands in for the network in unit tests."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if body is not None:
            return httpx.Response(
                200, content=body, headers={"content-type": "application/octet-stream"}
            )
        return httpx.Response(200, json=JSON_PAYLOAD, headers={"x-request-id": "req-9"})

    return httpx.MockTransport(handler)


def dead_transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("network must not be touched during replay")

    return httpx.MockTransport(handler)


async def test_record_captures_request_and_response() -> None:
    calls: list[httpx.Request] = []
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=real_transport(calls), runtime=runtime)
    )

    async with client:
        with record("case") as rec:
            response = await client.get(
                "https://api.example.test/orders/1",
                headers={"Authorization": "Bearer secret-token"},
            )

    assert response.status_code == 200
    assert response.json() == JSON_PAYLOAD
    assert len(calls) == 1

    (invocation,) = rec.trace.invocations
    assert invocation.boundary_type == "http.httpx"
    assert invocation.name == "GET /orders/1"
    assert invocation.request["method"] == "GET"
    assert invocation.request["url"] == "https://api.example.test/orders/1"
    assert invocation.request["headers"]["authorization"] == "[REDACTED]"
    assert invocation.response["status_code"] == 200
    assert invocation.response["json"] == JSON_PAYLOAD

    (span,) = rec.trace.spans
    assert span.kind.value == "http"


async def test_secret_headers_never_persist() -> None:
    calls: list[httpx.Request] = []
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=real_transport(calls), runtime=runtime)
    )

    async with client:
        with record("case") as rec:
            await client.get(
                "https://api.example.test/orders/1",
                headers={
                    "Authorization": "Bearer super-secret",
                    "X-API-Key": "key-123",
                    "Cookie": "session=abc",
                    "X-Custom": "visible",
                },
            )
    fixture = build_envelope(rec.trace)
    text = fixture.to_json_bytes().decode("utf-8")

    assert "super-secret" not in text
    assert "key-123" not in text
    assert "session=abc" not in text
    assert "visible" in text


async def test_replay_offline_without_socket() -> None:
    record_calls: list[httpx.Request] = []
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    recorder = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=real_transport(record_calls), runtime=runtime)
    )
    async with recorder:
        with record("case") as rec:
            await recorder.get("https://api.example.test/orders/1")
    fixture = build_envelope(rec.trace)

    replay_calls: list[httpx.Request] = []
    runtime2 = BoundaryRuntime(registry=registry)
    offline = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=dead_transport(replay_calls), runtime=runtime2)
    )
    from tracefork.replay import ReplaySession

    session = ReplaySession(fixture=fixture, registry=registry)
    async with offline:
        with session:
            response = await offline.get("https://api.example.test/orders/1")

    assert response.status_code == 200
    assert response.json() == JSON_PAYLOAD
    assert response.headers["x-request-id"] == "req-9"
    assert replay_calls == []
    assert session.result.matched == 1


async def test_replay_mismatch_fails_closed() -> None:
    record_calls: list[httpx.Request] = []
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    recorder = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=real_transport(record_calls), runtime=runtime)
    )
    async with recorder:
        with record("case") as rec:
            await recorder.get("https://api.example.test/orders/1")
    fixture = build_envelope(rec.trace)

    from tracefork.errors import ReplayMismatchError
    from tracefork.replay import ReplaySession

    replay_calls: list[httpx.Request] = []
    runtime2 = BoundaryRuntime(registry=registry)
    offline = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=dead_transport(replay_calls), runtime=runtime2)
    )
    session = ReplaySession(fixture=fixture, registry=registry)
    async with offline:
        with session, pytest.raises(ReplayMismatchError):
            await offline.post("https://api.example.test/orders/1", json={"a": 1})
    assert replay_calls == []


async def test_normal_mode_passthrough(echo_registry: BoundaryRegistry) -> None:
    calls: list[httpx.Request] = []
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=real_transport(calls), runtime=runtime)
    )
    async with client:
        response = await client.get("https://api.example.test/orders/1")
    assert response.json() == JSON_PAYLOAD
    assert len(calls) == 1


async def test_binary_bodies_round_trip_losslessly() -> None:
    """Octet-stream bodies must survive record→replay byte-for-byte."""
    binary = bytes(range(256))
    calls: list[httpx.Request] = []
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=real_transport(calls, binary), runtime=runtime)
    )

    async with client:
        with record("case") as rec:
            await client.post(
                "https://api.example.test/upload",
                content=binary,
                headers={"content-type": "application/octet-stream"},
            )

    (invocation,) = rec.trace.invocations
    assert invocation.request["content_base64"] is not None

    from tracefork.replay import ReplaySession

    replay_calls: list[httpx.Request] = []
    runtime2 = BoundaryRuntime(registry=registry)
    offline = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=dead_transport(replay_calls), runtime=runtime2)
    )
    session = ReplaySession(fixture=build_envelope(rec.trace), registry=registry)
    async with offline:
        with session:
            response = await offline.post(
                "https://api.example.test/upload",
                content=binary,
                headers={"content-type": "application/octet-stream"},
            )
    assert response.content == binary  # replayed response is byte-identical
    assert replay_calls == []


async def test_content_encoding_header_does_not_break_replay() -> None:
    """Real transports auto-decompress but leave content-encoding headers.

    Recording must strip content-coding headers, otherwise the replayed
    httpx.Response decompresses already-decompressed content and crashes
    (found by the live smoke against a real API).
    """
    import gzip as gzip_module

    calls: list[httpx.Request] = []
    compressed = gzip_module.compress(json.dumps(JSON_PAYLOAD).encode("utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # Simulate a decompressing transport: the returned object carries
        # decoded content but stale wire headers (set post-construction).
        response = httpx.Response(200, content=json.dumps(JSON_PAYLOAD).encode("utf-8"))
        response.headers["content-encoding"] = "gzip"
        response.headers["content-length"] = str(len(compressed))
        return response

    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=httpx.MockTransport(handler), runtime=runtime)
    )

    async with client:
        with record("case") as rec:
            response = await client.get("https://api.example.test/data")
    assert response.json() == JSON_PAYLOAD
    (invocation,) = rec.trace.invocations
    assert "content-encoding" not in invocation.response["headers"]

    from tracefork.replay import ReplaySession

    replay_calls: list[httpx.Request] = []
    runtime2 = BoundaryRuntime(registry=registry)
    offline = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=dead_transport(replay_calls), runtime=runtime2)
    )
    session = ReplaySession(fixture=build_envelope(rec.trace), registry=registry)
    async with offline:
        with session:
            replayed = await offline.get("https://api.example.test/data")
    assert replayed.status_code == 200
    assert replayed.json() == JSON_PAYLOAD  # must not raise DecodingError
    assert replay_calls == []
