"""TF-031: boundary registry."""

import pytest
from tracefork.boundaries import BoundaryHandler, BoundaryRegistry
from tracefork.boundaries.errors import UnknownBoundaryError
from tracefork.models import BoundaryRequest, BoundaryResponse


class EchoHandler:
    """Test handler: executes live and echoes the response payload back."""

    async def execute(self, request: BoundaryRequest, call_live) -> BoundaryResponse:
        return BoundaryResponse(response=await call_live(), metadata={"echo": True})

    def restore(self, response, metadata):
        return response


def test_handler_satisfies_protocol() -> None:
    assert isinstance(EchoHandler(), BoundaryHandler)


def test_register_and_lookup() -> None:
    registry = BoundaryRegistry()
    handler = EchoHandler()
    registry.register("llm.openai", handler)
    assert registry.handler_for("llm.openai") is handler


def test_unknown_boundary_type_raises() -> None:
    registry = BoundaryRegistry()
    with pytest.raises(UnknownBoundaryError):
        registry.handler_for("tool.unknown")


def test_types_are_sorted() -> None:
    registry = BoundaryRegistry()
    registry.register("tool.b", EchoHandler())
    registry.register("tool.a", EchoHandler())
    assert registry.types() == ["tool.a", "tool.b"]
