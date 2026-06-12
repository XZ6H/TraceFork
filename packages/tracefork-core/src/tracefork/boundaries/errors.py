"""Boundary-layer errors."""

from tracefork.errors import AdapterError


class UnknownBoundaryError(AdapterError):
    """Raised when no handler is registered for a boundary type."""
