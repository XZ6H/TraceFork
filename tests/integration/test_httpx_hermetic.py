"""TF-082: hermetic HTTP replay against a real server lifecycle.

Start a real HTTP server, record traffic against it, shut the server down,
and verify replay still succeeds with zero sockets — the plan's definition of
demonstrable hermetic behavior.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
from tracefork import record
from tracefork.adapters.httpx import TraceForkAsyncTransport
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.replay import ReplaySession
from tracefork.serialization import build_envelope


class _FakeAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"orders": [{"id": 31991, "total": 149.0}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-Id", "srv-42")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # silence test output
        return


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _real_http_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport()


def _dead_transport() -> httpx.AsyncBaseTransport:
    class _Dead(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise AssertionError("replay must not open a socket")

    return _Dead()


async def test_record_against_live_server_then_replay_offline() -> None:
    server, base_url = _start_server()
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    client = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=_real_http_transport(), runtime=runtime)
    )
    try:
        async with client:
            with record("orders-case") as rec:
                response = await client.get(f"{base_url}/orders/31991")
        assert response.status_code == 200
        assert response.json()["orders"][0]["id"] == 31991
    finally:
        server.shutdown()
        server.server_close()

    fixture = build_envelope(rec.trace)

    # Server is now shut down. Replay must still succeed, with no sockets.
    runtime2 = BoundaryRuntime(registry=registry)
    offline = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=_dead_transport(), runtime=runtime2)
    )
    session = ReplaySession(fixture=fixture, registry=registry)
    async with offline:
        with session:
            replayed = await offline.get(f"{base_url}/orders/31991")

    assert replayed.status_code == 200
    assert replayed.json() == {"orders": [{"id": 31991, "total": 149.0}]}
    assert replayed.headers["x-request-id"] == "srv-42"
    assert session.result.is_hermetic is True
    assert session.result.network_calls == 0
