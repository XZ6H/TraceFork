"""Boundary runtime (TF-030, TF-032, TF-033).

The runtime owns every mode decision (ADR 0004). Handlers translate between
native and canonical data; the runtime records, matches and fails closed.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal

from tracefork.boundaries.base import LiveCall, boundary_span_kind
from tracefork.boundaries.registry import BoundaryRegistry
from tracefork.canonicalization import Canonicalizer, canonicalize
from tracefork.errors import AdapterError, RecordingError, ReplayError, ReplayPolicyError
from tracefork.models import (
    BoundaryInvocation,
    BoundaryRequest,
    ExecutionMode,
    ReplayMode,
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

    def __init__(
        self, registry: BoundaryRegistry, canonicalizer: Canonicalizer | None = None
    ) -> None:
        self._registry = registry
        self._canonicalizer = canonicalizer if canonicalizer is not None else Canonicalizer()

    @property
    def registry(self) -> BoundaryRegistry:
        """The registry this runtime resolves handlers against."""
        return self._registry

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
        records the invocation. REPLAY resolves the replay policy per boundary:
        REPLAY boundaries are served from the recording (no live call exists
        on that path), LIVE boundaries execute for real, and unsupported modes
        fail closed (ADR 0003).
        """
        context = current_execution.get()
        mode = ExecutionMode.NORMAL if context is None else context.mode
        if mode is ExecutionMode.NORMAL:
            return await call_live()
        if mode is ExecutionMode.REPLAY:
            if context is None or context.replay_session is None:
                msg = "no replay session attached: refusing to call live (fail closed)"
                raise ReplayError(msg)
            session = context.replay_session
            policy = context.policy if context.policy is not None else session.policy
            boundary_mode = policy.mode_for(boundary_type, name)
            if boundary_mode is ReplayMode.REPLAY:
                return await session.replay_invoke(
                    boundary_type, name, request, dict(metadata or {})
                )
            if boundary_mode is ReplayMode.LIVE:
                if context.recording is None:
                    msg = "live boundaries in replay require a candidate recording"
                    raise RecordingError(msg)
                return await self._invoke_recording(
                    context.recording,
                    boundary_type,
                    name,
                    request,
                    call_live,
                    dict(metadata or {}),
                    on_live=lambda: session.note_live(boundary_type, name),
                )
            msg = f"replay mode {boundary_mode.value!r} is not supported yet"
            raise ReplayPolicyError(msg)
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
        on_live: Callable[[], None] | None = None,
    ) -> Any:
        # Canonicalize and fingerprint before anything is recorded: a request
        # that cannot be canonicalized must fail without side effects.
        try:
            canonical_request = self._canonicalizer.canonical_request(request)
            fingerprint = self._canonicalizer.fingerprint(boundary_type, name, request)
        except TypeError as exc:
            msg = f"boundary {boundary_type}.{name} request is not canonicalizable: {exc}"
            raise AdapterError(msg) from exc

        handler = self._registry.handler_for(boundary_type)
        boundary_request = BoundaryRequest(
            boundary_type=boundary_type,
            name=name,
            request=canonical_request,
            metadata=dict(metadata),
        )
        parent = current_span.get()
        parent_span_id = parent.span_id if parent is not None else None
        parent_name = parent.name if parent is not None else None
        span = recording.start_span(
            name, boundary_span_kind(boundary_type), input=canonical_request
        )
        token = current_span.set(span)
        try:
            try:
                if on_live is not None:
                    on_live()
                response = await handler.execute(boundary_request, call_live)
            except BaseException as exc:
                recording.finish_span(span, exc)
                recording.record_invocation(
                    BoundaryInvocation(
                        boundary_type=boundary_type,
                        name=name,
                        request=canonical_request,
                        response=None,
                        fingerprint=fingerprint,
                        span_id=span.span_id,
                        parent_span_id=parent_span_id,
                        parent_name=parent_name,
                        occurrence=recording.next_occurrence(
                            boundary_type, name, fingerprint, parent_span_id
                        ),
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
            # Responses must be JSON-safe too (adapters.md): a response that
            # cannot be canonicalized is an adapter contract violation — fail
            # here and persist nothing (the span is withdrawn).
            try:
                response.response = canonicalize(response.response)
            except TypeError as exc:
                recording.trace.spans.remove(span)
                msg = (
                    f"boundary {boundary_type}.{name} response is not "
                    f"canonicalizable: {exc}"
                )
                raise AdapterError(msg) from exc
            span.output = response.response
            recording.finish_span(span, None)
            recording.record_invocation(
                BoundaryInvocation(
                    boundary_type=boundary_type,
                    name=name,
                    request=canonical_request,
                    response=response.response,
                    fingerprint=fingerprint,
                    span_id=span.span_id,
                    parent_span_id=parent_span_id,
                    parent_name=parent_name,
                    occurrence=recording.next_occurrence(
                        boundary_type, name, fingerprint, parent_span_id
                    ),
                    metadata={**metadata, **response.metadata},
                )
            )
            # Callers always receive the native shape, in RECORD and REPLAY
            # alike; only the stored payload stays canonical (TF-073).
            return handler.restore(response.response, response.metadata)
        finally:
            current_span.reset(token)
