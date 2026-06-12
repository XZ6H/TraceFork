"""Boundary abstraction primitives (TF-030, ADR 0004).

Handlers belong to adapters: they translate between native calls and canonical
boundary data. They never decide modes or implement replay semantics — that is
exclusively the runtime's job.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from tracefork.models import BoundaryRequest, BoundaryResponse, SpanKind

LiveCall = Callable[[], Awaitable[Any]]


@runtime_checkable
class BoundaryHandler(Protocol):
    """Adapter-side translation contract.

    ``execute`` runs the real call and translates its response to canonical
    data. ``restore`` translates a recorded response payload back to the
    native shape so application code keeps working in replay (plan TF-073).
    """

    async def execute(self, request: BoundaryRequest, call_live: LiveCall) -> BoundaryResponse:
        """Execute the live call and return its canonical response."""
        ...

    def restore(self, response: Any, metadata: dict[str, Any]) -> Any:
        """Rebuild the native response object from a recorded payload."""
        ...


_KIND_BY_FAMILY: dict[str, SpanKind] = {
    "agent": SpanKind.AGENT,
    "llm": SpanKind.LLM,
    "tool": SpanKind.TOOL,
    "http": SpanKind.HTTP,
    "retriever": SpanKind.RETRIEVER,
}


def boundary_family(boundary_type: str) -> str:
    """Return the family part of a boundary type (``llm.openai`` -> ``llm``)."""
    return boundary_type.split(".", 1)[0]


def boundary_span_kind(boundary_type: str) -> SpanKind:
    """Map a boundary type to the span kind recorded for it."""
    return _KIND_BY_FAMILY.get(boundary_family(boundary_type), SpanKind.CUSTOM)
