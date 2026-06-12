"""Boundary registry (TF-031)."""

from tracefork.boundaries.base import BoundaryHandler
from tracefork.boundaries.errors import UnknownBoundaryError


class BoundaryRegistry:
    """Maps boundary types to adapter handlers.

    Adapters register themselves at import time or via explicit setup; the
    runtime consults the registry per invocation.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, BoundaryHandler] = {}

    def register(self, boundary_type: str, handler: BoundaryHandler) -> None:
        if not boundary_type:
            msg = "boundary_type must be a non-empty string"
            raise ValueError(msg)
        self._handlers[boundary_type] = handler

    def handler_for(self, boundary_type: str) -> BoundaryHandler:
        try:
            return self._handlers[boundary_type]
        except KeyError:
            msg = (
                f"no handler registered for boundary type {boundary_type!r} "
                f"(registered: {self.types()})"
            )
            raise UnknownBoundaryError(msg) from None

    def types(self) -> list[str]:
        return sorted(self._handlers)
