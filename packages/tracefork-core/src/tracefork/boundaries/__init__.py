"""Boundary abstraction: runtime, registry and adapter contract (ADR 0004)."""

from tracefork.boundaries.base import BoundaryHandler, LiveCall, boundary_span_kind
from tracefork.boundaries.registry import BoundaryRegistry
from tracefork.boundaries.runtime import BoundaryRuntime, execution_context
from tracefork.models import ExecutionMode, ReplayMode, ReplayPolicy
from tracefork.recording.context import ExecutionContext

__all__ = [
    "BoundaryHandler",
    "BoundaryRegistry",
    "BoundaryRuntime",
    "ExecutionContext",
    "ExecutionMode",
    "LiveCall",
    "ReplayMode",
    "ReplayPolicy",
    "boundary_span_kind",
    "execution_context",
]
