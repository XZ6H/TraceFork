"""Boundary runtime (TF-030, TF-032, TF-033).

The runtime owns every mode decision (ADR 0004). Handlers translate between
native and canonical data; the runtime records, matches and fails closed.
"""

from __future__ import annotations

from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal

from tracefork.boundaries.base import LiveCall, boundary_span_kind
from tracefork.boundaries.registry import BoundaryRegistry
from tracefork.errors import RecordingError, ReplayError
from tracefork.models import (
    BoundaryInvocation,
    BoundaryRequest,
    ExecutionMode,
)
from tracefork.recording.context import ExecutionContext, current_execution, current_span

if TYPE_CHECKING:
    from tracefork.recording.recorder import Recording


class _ExecutionContextManager:
    """Dual sync/async context manager activating an :class:`ExecutionContext`."""

    def __init__(self, context: ExecutionContext) -> None:
        self._context = context
        self._token: Any = None

    def __enter__(self) -> ExecutionContext:
        self._token = current_execution.set(self._context)
        return self._context

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if self._token is not None:
            current_execution.reset(self._token)
            self._token = None
        return False

    async def __aenter__(self) -> ExecutionContext:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        return self.__exit__(exc_type, exc_value, traceback)


def execution_context(context: ExecutionContext) -> _ExecutionContextManager:
    """Activate an execution context for the duration of the block."""
    return _ExecutionContextManager(context)


class BoundaryRuntime:
    """Routes boundary invocations according to the active execution context."""

    def __init__(self, registry: BoundaryRegistry) -> None:
        self._registry = registry

    async def invoke(
        self,
        boundary_type: str,
        name: str,
        request: Any,
        call_live: LiveCall,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke one boundary under the current execution mode.

        NORMAL passes through untouched. RECORD executes live exactly once and
        records the invocation. REPLAY without a session fails closed: the
        live call is never made (ADR 0003).
        """
        context = current_execution.get()
        mode = ExecutionMode.NORMAL if context is None else context.mode
        if mode is ExecutionMode.NORMAL:
            return await call_live()
        if mode is ExecutionMode.REPLAY:
            msg = "no replay session attached: refusing to call live (fail closed)"
            raise ReplayError(msg)
        if context is None or context.recording is None:
            msg = "record mode requires an active recording"
            raise RecordingError(msg)
        return await self._invoke_recording(
            context.recording, boundary_type, name, request, call_live, dict(metadata or {})
        )

    async def _invoke_recording(
        self,
        recording: Recording,
        boundary_type: str,
        name: str,
        request: Any,
        call_live: LiveCall,
        metadata: dict[str, Any],
    ) -> Any:
        handler = self._registry.handler_for(boundary_type)
        boundary_request = BoundaryRequest(
            boundary_type=boundary_type, name=name, request=request, metadata=dict(metadata)
        )
        parent = current_span.get()
        parent_span_id = parent.span_id if parent is not None else None
        span = recording.start_span(name, boundary_span_kind(boundary_type), input=request)
        token = current_span.set(span)
        try:
            try:
                response = await handler.execute(boundary_request, call_live)
            except BaseException as exc:
                recording.finish_span(span, exc)
                recording.trace.invocations.append(
                    BoundaryInvocation(
                        boundary_type=boundary_type,
                        name=name,
                        request=request,
                        response=None,
                        span_id=span.span_id,
                        parent_span_id=parent_span_id,
                        occurrence=recording.next_occurrence(boundary_type, name, parent_span_id),
                        metadata={
                            **metadata,
                            "error": {
                                "type": type(exc).__qualname__,
                                "message": str(exc),
                            },
                        },
                    )
                )
                raise
            span.output = response.response
            recording.finish_span(span, None)
            recording.trace.invocations.append(
                BoundaryInvocation(
                    boundary_type=boundary_type,
                    name=name,
                    request=request,
                    response=response.response,
                    span_id=span.span_id,
                    parent_span_id=parent_span_id,
                    occurrence=recording.next_occurrence(boundary_type, name, parent_span_id),
                    metadata={**metadata, **response.metadata},
                )
            )
            return response.response
        finally:
            current_span.reset(token)
