"""Recording context propagation (TF-020, TF-032).

State lives in contextvars, never in global mutable state, so concurrent
recordings are isolated and async task trees inherit their enclosing
recording/span automatically.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tracefork.models import ExecutionMode, ReplayPolicy

if TYPE_CHECKING:
    from tracefork.models import Span
    from tracefork.recording.recorder import Recording
    from tracefork.replay.session import ReplaySession

current_recording: ContextVar["Recording | None"] = ContextVar(
    "tracefork_current_recording", default=None
)
current_span: ContextVar["Span | None"] = ContextVar("tracefork_current_span", default=None)
current_execution: ContextVar["ExecutionContext | None"] = ContextVar(
    "tracefork_current_execution", default=None
)


@dataclass
class ExecutionContext:
    """Execution mode and its attached state (TF-032).

    One context is active per task at a time; ``record()`` installs a RECORD
    context, replay executions install a REPLAY context with a session and
    policy.
    """

    mode: ExecutionMode
    recording: "Recording | None" = None
    policy: ReplayPolicy | None = None
    replay_session: "ReplaySession | None" = None
