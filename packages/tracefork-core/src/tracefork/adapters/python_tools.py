"""Python function tool adapter (TF-060..063).

Python tools are ordinary functions routed through the boundary runtime, so
recording, matching and replay semantics live entirely in the core (ADR 0004).
The adapter only translates: argument canonicalization on the way in, the
recorded response on the way out.

Usage::

    runtime = BoundaryRuntime(registry=registry)
    tools = ToolBox(runtime)

    @tools.tool()
    async def search_orders(customer_id: int) -> dict:
        ...

    result = await search_orders(customer_id=912)   # recorded or replayed
"""

import dataclasses
import functools
import inspect
from collections.abc import Callable
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from tracefork.boundaries import BoundaryRuntime
from tracefork.boundaries.base import LiveCall
from tracefork.canonicalization import canonical_json
from tracefork.errors import AdapterError
from tracefork.models import BoundaryRequest, BoundaryResponse

TOOL_BOUNDARY_TYPE = "tool.python"


class PythonToolHandler:
    """Generic boundary handler for Python tools: run live, restore verbatim."""

    async def execute(self, request: BoundaryRequest, call_live: LiveCall) -> BoundaryResponse:
        return BoundaryResponse(response=await call_live(), metadata={})

    def restore(self, response: Any, metadata: dict[str, Any]) -> Any:
        return response


def canonicalize_argument(value: Any) -> Any:
    """Convert a tool argument to a canonical, JSON-ready value (TF-063).

    Supports primitives, datetimes, UUIDs, enums, dataclasses, Pydantic
    models, mappings and sequences (including deterministically ordered sets).
    Anything else raises ``AdapterError`` — objects are never silently
    stringified.
    """
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int | float):
        return value  # finite check happens in canonicalization.json
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return canonicalize_argument(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: canonicalize_argument(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, BaseModel):
        return canonicalize_argument(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(key): canonicalize_argument(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [canonicalize_argument(item) for item in value]
    if isinstance(value, set | frozenset):
        items = [canonicalize_argument(item) for item in value]
        return sorted(items, key=canonical_json)
    msg = (
        f"tool argument of type {type(value).__name__} cannot be canonicalized; "
        "pass JSON-compatible data, dataclasses, Pydantic models, enums, "
        "datetimes, UUIDs or collections thereof"
    )
    raise AdapterError(msg)


class WrappedTool:
    """A Python function routed through the boundary runtime.

    Calling the wrapper records the call (RECORD mode), replays it
    (REPLAY + policy REPLAY), or executes it live per the active context. The
    real function body is unreachable on the replay path (TF-062).
    """

    def __init__(
        self,
        func: Callable[..., Any],
        runtime: BoundaryRuntime,
        name: str | None = None,
        boundary_type: str = TOOL_BOUNDARY_TYPE,
    ) -> None:
        self._func = func
        self._runtime = runtime
        self.tool_name = name if name is not None else f"{func.__module__}.{func.__qualname__}"
        self._boundary_type = boundary_type
        functools.update_wrapper(self, func, updated=())

    @property
    def boundary_type(self) -> str:
        return self._boundary_type

    @property
    def func(self) -> Callable[..., Any]:
        return self._func

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        request = {
            "args": [canonicalize_argument(arg) for arg in args],
            "kwargs": {str(key): canonicalize_argument(value) for key, value in kwargs.items()},
        }

        async def call_live() -> Any:
            result = self._func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

        return await self._runtime.invoke(self.boundary_type, self.tool_name, request, call_live)


class ToolBox:
    """Binds tool decorators to a boundary runtime and registers the handler.

    Dependency-injected by design: there is no global registry and no hidden
    mutation. The shared :class:`PythonToolHandler` is registered once.
    """

    def __init__(self, runtime: BoundaryRuntime) -> None:
        self._runtime = runtime
        self._handler_registered = False

    def tool(
        self, *, name: str | None = None, boundary_type: str = TOOL_BOUNDARY_TYPE
    ) -> Callable[[Callable[..., Any]], WrappedTool]:
        """Decorate an async or sync function as a TraceFork tool.

        ``boundary_type`` re-labels the boundary family (e.g. ``llm.demo`` for
        a scripted LLM); it decides the span kind and replay family.
        """

        def decorator(func: Callable[..., Any]) -> WrappedTool:
            return self.wrap(func, name=name, boundary_type=boundary_type)

        return decorator

    def wrap(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        boundary_type: str = TOOL_BOUNDARY_TYPE,
    ) -> WrappedTool:
        """Wrap an existing function as a TraceFork tool."""
        self._ensure_handler(boundary_type)
        return WrappedTool(func, self._runtime, name=name, boundary_type=boundary_type)

    def _ensure_handler(self, boundary_type: str) -> None:
        if boundary_type == TOOL_BOUNDARY_TYPE:
            if not self._handler_registered:
                self._runtime.registry.register(boundary_type, PythonToolHandler())
                self._handler_registered = True
            return
        # Custom boundary types get their own handler registration.
        self._runtime.registry.register(boundary_type, PythonToolHandler())


__all__ = [
    "TOOL_BOUNDARY_TYPE",
    "PythonToolHandler",
    "ToolBox",
    "WrappedTool",
    "canonicalize_argument",
]
