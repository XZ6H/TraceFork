"""httpx adapter (TF-080..082).

An ``httpx.AsyncClient`` transport that routes every request through the
boundary runtime, so HTTP traffic is recorded and replayed hermetically.
Secret headers are redacted before anything is persisted (TF-081). Async-only
in v0.1: the boundary runtime is async-first.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
from tracefork.boundaries import BoundaryRuntime
from tracefork.boundaries.base import LiveCall
from tracefork.models import BoundaryRequest, BoundaryResponse

HTTPX_BOUNDARY_TYPE = "http.httpx"

REDACTED = "[REDACTED]"

# Secret-safe defaults (TF-081): matched case-insensitively.
_SECRET_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}


class HTTPXHandler:
    """Translates httpx request/response objects to and from canonical data."""

    async def execute(self, request: BoundaryRequest, call_live: LiveCall) -> BoundaryResponse:
        response: httpx.Response = await call_live()
        await response.aread()  # live responses stream; reading is a no-op otherwise
        return BoundaryResponse(
            response=_response_payload(request.request, response),
            metadata={"status_code": response.status_code},
        )

    def restore(self, response: dict[str, Any], metadata: dict[str, Any]) -> httpx.Response:
        recorded_request = response["request"]
        request = httpx.Request(
            method=recorded_request["method"],
            url=recorded_request["url"],
            headers=_plain_headers(recorded_request["headers"]),
            content=_request_content(recorded_request),
        )
        status: int = response["status_code"]
        return httpx.Response(
            status_code=status,
            headers=_plain_headers(response["headers"]),
            content=_response_content(response),
            request=request,
        )


class TraceForkAsyncTransport(httpx.AsyncBaseTransport):
    """httpx transport that routes requests through the boundary runtime.

    Wrap the real transport::

        client = httpx.AsyncClient(
            transport=TraceForkAsyncTransport(inner=httpx.AsyncTransport(), runtime=runtime)
        )
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, runtime: BoundaryRuntime) -> None:
        self._inner = inner
        self._runtime = runtime
        runtime.registry.register(HTTPX_BOUNDARY_TYPE, HTTPXHandler())

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        payload = _request_payload(request)
        name = f"{request.method} {request.url.path}"

        async def call_live() -> httpx.Response:
            return await self._inner.handle_async_request(request)

        response: httpx.Response = await self._runtime.invoke(
            HTTPX_BOUNDARY_TYPE, name, payload, call_live
        )
        return response


def _request_payload(request: httpx.Request) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "method": request.method,
        "url": str(request.url),
        "headers": _safe_headers(request.headers),
    }
    body = _decode_body(request.headers.get("content-type"), request.content)
    if body is not None:
        payload.update(body)
    return payload


def _response_payload(request_payload: dict[str, Any], response: httpx.Response) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request": request_payload,
        "status_code": response.status_code,
        "headers": _safe_headers(response.headers),
    }
    body = _decode_body(response.headers.get("content-type"), response.content)
    if body is not None:
        payload.update(body)
    return payload


def _decode_body(content_type: str | None, content: bytes) -> dict[str, Any] | None:
    if not content:
        return None
    if content_type and "application/json" in content_type:
        try:
            return {"json": json.loads(content)}
        except json.JSONDecodeError:
            pass
    try:
        return {"content": content.decode("utf-8")}
    except UnicodeDecodeError:
        # Binary bodies are stored base64 so replays are byte-identical
        # (errors="replace" would silently corrupt them).
        return {"content_base64": base64.b64encode(content).decode("ascii")}


def _request_content(recorded_request: dict[str, Any]) -> bytes:
    if "json" in recorded_request:
        return str(json.dumps(recorded_request["json"])).encode("utf-8")
    if "content" in recorded_request:
        return str(recorded_request["content"]).encode("utf-8")
    if "content_base64" in recorded_request:
        return base64.b64decode(recorded_request["content_base64"])
    return b""


def _response_content(recorded_response: dict[str, Any]) -> bytes:
    if "json" in recorded_response:
        return str(json.dumps(recorded_response["json"])).encode("utf-8")
    if "content" in recorded_response:
        return str(recorded_response["content"]).encode("utf-8")
    if "content_base64" in recorded_response:
        return base64.b64decode(recorded_response["content_base64"])
    return b""


def _safe_headers(headers: Any) -> dict[str, str]:
    """Lower-case header map with secret headers redacted (TF-081)."""
    return {
        str(key).lower(): REDACTED if str(key).lower() in _SECRET_HEADERS else str(value)
        for key, value in headers.items()
    }


def _plain_headers(headers: dict[str, str]) -> list[tuple[str, str]]:
    return list(headers.items())
