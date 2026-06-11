"""Recording engine: sessions and span lifecycle."""

from tracefork.recording.context import current_recording, current_span
from tracefork.recording.lifecycle import span
from tracefork.recording.recorder import Recording, record

__all__ = [
    "Recording",
    "current_recording",
    "current_span",
    "record",
    "span",
]
