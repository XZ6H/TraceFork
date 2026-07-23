"""Item 6a: sync httpx transport — record/replay fidelity and loop guard."""

from typing import Any

import httpx
import pytest
from tracefork import record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.errors import AdapterError
from tracefork.replay import ReplaySession
from tracefork.serialization import build_envelope
from tracefork_httpx import TraceForkTransport

JSON_PAYLOAD: dict[str, Any] = {"orders": [{"id": 1}]}


def real_sync(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=JSON_PAYLOAD)

    return httpx.MockTransport(handler)


def dead_sync(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("network touched during replay")

    return httpx.MockTransport(handler)


def test_sync_record_and_replay(tmp_path) -> None:
    record_calls: list[httpx.Request] = []
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = httpx.Client(
        transport=TraceForkTransport(inner=real_sync(record_calls), runtime=runtime)
    )
    with client, record("sync-case") as rec:
        response = client.get("https://api.example.test/orders/1")
    assert response.json() == JSON_PAYLOAD
    assert len(record_calls) == 1

    replay_calls: list[httpx.Request] = []
    runtime2 = BoundaryRuntime(registry=registry)
    offline = httpx.Client(
        transport=TraceForkTransport(inner=dead_sync(replay_calls), runtime=runtime2)
    )
    session = ReplaySession(fixture=build_envelope(rec.trace), registry=registry)
    with offline, session:
        replayed = offline.get("https://api.example.test/orders/1")
    assert replayed.json() == JSON_PAYLOAD
    assert replay_calls == []
    assert session.result.matched == 1


async def test_sync_transport_inside_running_loop_raises() -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = httpx.Client(transport=TraceForkTransport(inner=real_sync([]), runtime=runtime))
    with client, pytest.raises(AdapterError, match="running event loop"):
        client.get(  # noqa: ASYNC212 -- the blocking call IS the behavior under test
            "https://api.example.test/orders/1"
        )
