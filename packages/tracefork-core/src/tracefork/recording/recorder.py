"""Recording engine (TF-020).

``record()`` opens a recording session; every span started inside the context
is appended to the session's trace. The context manager is dual-mode: it works
as a sync ``with`` and an async ``async with``.
"""

import uuid
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Literal

from tracefork.models import (
    ExecutionMode,
    Provenance,
    Span,
    SpanError,
    SpanKind,
    SpanStatus,
    Trace,
    TraceStatus,
    capture_git_provenance,
)
from tracefork.recording.context import (
    ExecutionContext,
    current_execution,
    current_recording,
    current_span,
)


class Recording:
    """An in-progress recording session yielded by :func:`record`."""

    def __init__(
        self,
        name: str,
        *,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        capture_provenance: bool = True,
    ) -> None:
        self.trace = Trace(
            trace_id=f"tr_{uuid.uuid4().hex}",
            name=name,
            started_at=datetime.now(UTC),
            input=input,
            metadata=dict(metadata) if metadata else {},
            provenance=capture_git_provenance() if capture_provenance else Provenance(),
        )
        self._finished = False
        self._occurrence_counts: dict[tuple[str, str, str | None], int] = {}

    def start_span(self, name: str, kind: SpanKind, input: Any = None) -> Span:
        """Start a span parented to the currently open span (if any)."""
        parent = current_span.get()
        span = Span(
            span_id=f"sp_{uuid.uuid4().hex}",
            parent_span_id=parent.span_id if parent is not None else None,
            kind=kind,
            name=name,
            input=input,
            started_at=datetime.now(UTC),
        )
        # Append order is start order, never completion order (ADR 0005).
        self.trace.spans.append(span)
        return span

    def finish_span(self, span: Span, error: BaseException | None) -> None:
        """Close a span, recording the exception when one escaped it (TF-024)."""
        span.completed_at = datetime.now(UTC)
        if error is None:
            span.status = SpanStatus.OK
            return
        span.status = SpanStatus.ERROR
        # Exception type and message only: stack traces carry local paths.
        span.error = SpanError(exception_type=type(error).__qualname__, message=str(error))

    def next_occurrence(self, boundary_type: str, name: str, parent_span_id: str | None) -> int:
        """Assign the occurrence index for a boundary call (TF-043).

        Scoped by boundary type, name and logical parent so repeated identical
        calls stay distinguishable. Fingerprints join the scope in M4.
        """
        key = (boundary_type, name, parent_span_id)
        count = self._occurrence_counts.get(key, 0)
        self._occurrence_counts[key] = count + 1
        return count

    def finish(self, error: BaseException | None = None) -> None:
        """Close the recording session."""
        if self._finished:
            return
        self._finished = True
        self.trace.completed_at = datetime.now(UTC)
        self.trace.status = TraceStatus.FAILED if error is not None else TraceStatus.COMPLETED


class _RecordingContext:
    """Dual sync/async context manager driving a :class:`Recording`.

    Installing a recording also installs a RECORD execution context, so
    boundary calls made inside the block are captured automatically.
    """

    def __init__(self, recording: Recording) -> None:
        self.recording = recording
        self._recording_token: Any = None
        self._execution_token: Any = None

    def __enter__(self) -> Recording:
        self._recording_token = current_recording.set(self.recording)
        self._execution_token = current_execution.set(
            ExecutionContext(mode=ExecutionMode.RECORD, recording=self.recording)
        )
        return self.recording

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if self._execution_token is not None:
            current_execution.reset(self._execution_token)
            self._execution_token = None
        if self._recording_token is not None:
            current_recording.reset(self._recording_token)
            self._recording_token = None
        self.recording.finish(exc_value)
        return False

    async def __aenter__(self) -> Recording:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        return self.__exit__(exc_type, exc_value, traceback)


def record(
    name: str,
    *,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    capture_provenance: bool = True,
) -> _RecordingContext:
    """Record one agent execution as a trace.

    Usage::

        with record("case") as recording:
            ...  # agent code; spans and boundaries are captured

        async with record("case") as recording:
            ...
    """
    return _RecordingContext(
        Recording(name, input=input, metadata=metadata, capture_provenance=capture_provenance)
    )
