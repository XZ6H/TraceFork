"""TraceFork error hierarchy.

All errors raised by TraceFork derive from :class:`TraceForkError`; underlying
library exceptions are never exposed as the public API (plan §35).
"""


class TraceForkError(Exception):
    """Base class for every error raised by TraceFork."""


class RecordingError(TraceForkError):
    """Raised when an execution cannot be recorded."""


class FixtureError(TraceForkError):
    """Base class for fixture-related failures."""


class FixtureNotFoundError(FixtureError):
    """Raised when a referenced fixture does not exist."""


class FixtureCorruptError(FixtureError):
    """Raised when a fixture is malformed or fails its integrity check."""


class FixtureVersionError(FixtureError):
    """Raised when a fixture uses an unsupported fixture/schema version."""


class ReplayError(TraceForkError):
    """Base class for replay-related failures."""


class ReplayMismatchError(ReplayError):
    """Raised when a live boundary call cannot be matched to a recording."""


class ReplayPolicyError(ReplayError):
    """Raised when a replay policy is invalid or cannot be resolved."""


class UnexpectedLiveCallError(ReplayError):
    """Raised when a boundary performs a live call in a mode that forbids it."""


class AdapterError(TraceForkError):
    """Raised when an adapter cannot translate a native call."""


class EvaluationError(TraceForkError):
    """Raised when an evaluation cannot be performed."""
