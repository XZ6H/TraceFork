"""Shared test fixtures."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from tracefork.boundaries import BoundaryRegistry
from tracefork.models import BoundaryRequest, BoundaryResponse

LiveCall = Callable[[], Awaitable[Any]]


class EchoHandler:
    """Test handler: executes the live call and echoes the response payload."""

    async def execute(self, request: BoundaryRequest, call_live: LiveCall) -> BoundaryResponse:
        return BoundaryResponse(response=await call_live(), metadata={"echo": True})

    def restore(self, response: Any, metadata: dict[str, Any]) -> Any:
        return response


@pytest.fixture
def echo_registry() -> BoundaryRegistry:
    registry = BoundaryRegistry()
    registry.register("tool.echo", EchoHandler())
    registry.register("llm.test", EchoHandler())
    return registry
