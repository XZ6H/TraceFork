"""OpenAI Responses API adapter (TF-070..074).

Routes ``client.responses.create`` (sync and async, streaming and not) through
the boundary runtime. The adapter only translates:

- native kwargs → canonical request payload,
- SDK response/stream objects → canonical payloads (with usage metadata),
- recorded payloads → SDK objects rebuilt via the SDK's own construction
  machinery, so application code keeps working unchanged in replay.

The live callable is supplied by the instrumentation and is structurally
unreachable on the replay path (ADR 0004, ADR 0003).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from openai import AsyncOpenAI, OpenAI
from openai._models import construct_type
from openai.types.responses import Response
from openai.types.responses.response_stream_event import ResponseStreamEvent

from tracefork.adapters import canonicalize_argument
from tracefork.boundaries import BoundaryRuntime
from tracefork.boundaries.base import LiveCall
from tracefork.errors import AdapterError
from tracefork.models import BoundaryRequest, BoundaryResponse

LLM_OPENAI_BOUNDARY_TYPE = "llm.openai"


class OpenAIHandler:
    """Translates Responses API traffic between native and canonical shapes."""

    async def execute(self, request: BoundaryRequest, call_live: LiveCall) -> BoundaryResponse:
        started = time.perf_counter()
        if request.metadata.get("stream"):
            stream = await _call(call_live)
            if hasattr(stream, "__aiter__"):
                events = [event.model_dump(mode="json") async for event in stream]
            else:
                events = [event.model_dump(mode="json") for event in stream]
            terminal_types = ("response.completed", "response.incomplete")
            terminal = next((e for e in reversed(events) if e.get("type") in terminal_types), None)
            usage = _usage_metadata((terminal or {}).get("response", {}).get("usage"))
            return BoundaryResponse(
                response={"__stream__": True, "events": events},
                metadata={
                    "stream": True,
                    "usage": usage,
                    "finish_reason": terminal.get("type").removeprefix("response.")
                    if terminal
                    else None,
                    "latency_ms": _latency_ms(started),
                },
            )
        response = await _call(call_live)
        payload = response.model_dump(mode="json")
        return BoundaryResponse(
            response=payload,
            metadata={
                "stream": False,
                "usage": _usage_metadata(payload.get("usage")),
                "finish_reason": payload.get("status"),
                "latency_ms": _latency_ms(started),
            },
        )

    def restore(self, response: Any, metadata: dict[str, Any]) -> Any:
        if metadata.get("stream"):
            events = [
                construct_type(type_=ResponseStreamEvent, value=event)
                for event in response["events"]
            ]
            return _ReplayedStream(events)
        return construct_type(type_=Response, value=response)


class _ReplayedStream:
    """In-memory stream replays a recorded event sequence.

    Buffers the full event list (also done at record time); supports both the
    sync and async iteration protocols, matching whichever client is used.
    """

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def __iter__(self) -> Iterator[Any]:
        return iter(self._events)

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _gen() -> AsyncIterator[Any]:
            for event in self._events:
                yield event

        return _gen()


def instrument_openai(client: OpenAI | AsyncOpenAI, runtime: BoundaryRuntime) -> None:
    """Route ``client.responses.create`` through the boundary runtime, in place.

    Registers the shared OpenAI handler on the runtime's registry. Recording,
    matching and replay semantics remain in the core runtime.
    """
    runtime.registry.register(LLM_OPENAI_BOUNDARY_TYPE, OpenAIHandler())
    original = client.responses.create
    is_async = isinstance(client, AsyncOpenAI)

    if is_async:

        async def create_async(**kwargs: Any) -> Any:
            return await _invoke(runtime, original, kwargs, sync_client=False)

        client.responses.create = create_async  # type: ignore[method-assign]
        return

    def create_sync(**kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            msg = "sync OpenAI client used inside a running event loop; use AsyncOpenAI"
            raise AdapterError(msg)
        return asyncio.run(_invoke(runtime, original, kwargs, sync_client=True))

    client.responses.create = create_sync  # type: ignore[method-assign]


async def _invoke(
    runtime: BoundaryRuntime,
    original: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    sync_client: bool,
) -> Any:
    model = kwargs.get("model") or "responses.create"
    stream = bool(kwargs.get("stream"))
    request_payload = {str(key): canonicalize_argument(value) for key, value in kwargs.items()}

    async def call_live() -> Any:
        result = original(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return await runtime.invoke(
        LLM_OPENAI_BOUNDARY_TYPE,
        model,
        request_payload,
        call_live,
        metadata={"stream": stream, "sync": sync_client},
    )


async def _call(call_live: LiveCall) -> Any:
    return await call_live()


def _usage_metadata(usage: dict[str, Any] | None) -> dict[str, Any]:
    if not usage:
        return {}
    input_details = usage.get("input_tokens_details") or {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cached_tokens": input_details.get("cached_tokens"),
    }


def _latency_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
