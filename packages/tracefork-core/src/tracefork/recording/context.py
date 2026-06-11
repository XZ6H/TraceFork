"""Recording context propagation (TF-020).

State lives in contextvars, never in global mutable state, so concurrent
recordings are isolated and async task trees inherit their enclosing
recording/span automatically.
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tracefork.models import Span
    from tracefork.recording.recorder import Recording

current_recording: ContextVar["Recording | None"] = ContextVar(
    "tracefork_current_recording", default=None
)
current_span: ContextVar["Span | None"] = ContextVar("tracefork_current_span", default=None)
