"""Span lifecycle helpers (TF-021, TF-022).

``span()`` opens a span inside the active recording. Outside a recording it is
a no-op, so instrumented agent code works with and without TraceFork enabled.
Dual sync/async usage like ``record()``.
"""

from types import TracebackType
from typing import Any, Literal

from tracefork.models import Span, SpanKind
from tracefork.recording.context import current_recording, current_span
from tracefork.recording.recorder import Recording


class _SpanContext:
    """Dual sync/async context manager for one span."""

    def __init__(self, name: str, kind: SpanKind, input: Any) -> None:
        self._name = name
        self._kind = kind
        self._input = input
        self._recording: Recording | None = None
        self._span: Span | None = None
        self._token: Any = None

    def __enter__(self) -> None:
        self._open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self._close(exc_value)
        return False

    async def __aenter__(self) -> None:
        self._open()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self._close(exc_value)
        return False

    def _open(self) -> None:
        recording = current_recording.get()
        if recording is None:
            return
        self._recording = recording
        self._span = recording.start_span(self._name, self._kind, self._input)
        self._token = current_span.set(self._span)

    def _close(self, error: BaseException | None) -> None:
        if self._token is not None:
            current_span.reset(self._token)
            self._token = None
        if self._recording is not None and self._span is not None:
            self._recording.finish_span(self._span, error)


def span(
    name: str,
    *,
    kind: SpanKind = SpanKind.CUSTOM,
    input: Any = None,
) -> _SpanContext:
    """Open a named span inside the active recording.

    Usage::

        with span("lookup-customer", kind=SpanKind.TOOL, input={...}):
            ...

        async with span("call-llm", kind=SpanKind.LLM):
            ...
    """
    return _SpanContext(name, kind, input)
