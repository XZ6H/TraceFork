"""TF-080..082: httpx adapter — interception, secret-safe defaults, hermetic replay."""

from typing import Any

import httpx
import pytest
from tracefork import record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.serialization import build_envelope
from tracefork_httpx import TraceForkAsyncTransport

JSON_PAYLOAD: dict[str, Any] = {"orders": [{"id": 1, "total": 149.0}]}


def real_transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    """Stands in for the network in unit tests."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
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
