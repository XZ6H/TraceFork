"""TF-060..063: Python tool adapter — decorator, recording, replay, arg canonicalization."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel
from tracefork import record
from tracefork.adapters import ToolBox
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.errors import AdapterError
from tracefork.serialization import build_envelope


@dataclass
class Customer:
    customer_id: int
    region: str


class Region(Enum):
    EU = "eu"
    US = "us"


class CustomerModel(BaseModel):
    customer_id: int
    region: str


def make_toolbox() -> tuple[ToolBox, BoundaryRegistry]:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    return ToolBox(runtime), registry


async def test_tool_call_is_recorded_with_fully_qualified_name() -> None:
    tools, _ = make_toolbox()

    @tools.tool()
    async def search_orders(customer_id: int) -> dict[str, Any]:
        return {"orders": [customer_id]}

    with record("case") as rec:
        result = await search_orders(customer_id=912)

    assert result == {"orders": [912]}
    (invocation,) = rec.trace.invocations
    assert invocation.boundary_type == "tool.python"
    assert invocation.name.endswith("search_orders")
    assert invocation.name.startswith("tests.unit.test_python_tools.")
    assert invocation.request == {"args": [], "kwargs": {"customer_id": 912}}
    assert invocation.response == {"orders": [912]}
    (span,) = rec.trace.spans
    assert span.kind.value == "tool"
    assert span.name == invocation.name
    assert span.status.value == "ok"


async def test_sync_tool_is_supported() -> None:
    tools, _ = make_toolbox()

    @tools.tool()
    def lookup(city: str) -> dict[str, Any]:
        return {"city": city}

    # The wrapper is async-first; the *body* may be sync.
    with record("case") as rec:
        result = await lookup("Berlin")
    assert result == {"city": "Berlin"}
    assert rec.trace.invocations[0].response == {"city": "Berlin"}


async def test_positional_args_are_recorded() -> None:
    tools, _ = make_toolbox()

    @tools.tool()
    async def add(a: int, b: int) -> int:
        return a + b

    with record("case") as rec:
        await add(2, b=3)
    # Arguments are captured exactly as Python binds them.
    assert rec.trace.invocations[0].request == {"args": [2], "kwargs": {"b": 3}}


async def test_dataclass_args_are_canonicalized() -> None:
    tools, _ = make_toolbox()

    @tools.tool()
    async def get_customer(customer: Customer) -> str:
        return customer.region

    with record("case") as rec:
        await get_customer(Customer(customer_id=912, region="eu"))
    assert rec.trace.invocations[0].request == {
        "args": [{"customer_id": 912, "region": "eu"}],
        "kwargs": {},
    }


async def test_pydantic_model_args_are_canonicalized() -> None:
    tools, _ = make_toolbox()

    @tools.tool()
    async def get_customer(customer: CustomerModel) -> int:
        return customer.customer_id

    with record("case") as rec:
        await get_customer(CustomerModel(customer_id=912, region="eu"))
    assert rec.trace.invocations[0].request["args"][0] == {
        "customer_id": 912,
        "region": "eu",
    }


async def test_enum_datetime_uuid_args_are_canonicalized() -> None:
    tools, _ = make_toolbox()

    @tools.tool()
    async def describe(region: Region, at: datetime, token: UUID) -> str:
        return f"{region.value}-{token}"

    token = UUID("12345678-1234-5678-1234-567812345678")
    with record("case") as rec:
        await describe(Region.EU, datetime(2026, 9, 2, 17, 0, tzinfo=UTC), token)
    request = rec.trace.invocations[0].request["args"]
    assert request[0] == "eu"
    assert request[1] == "2026-09-02T17:00:00Z"
    assert request[2] == "12345678-1234-5678-1234-567812345678"


async def test_sets_are_canonicalized_deterministically() -> None:
    tools, _ = make_toolbox()

    @tools.tool()
    async def count_tags(tags: set[str]) -> int:
        return len(tags)

    fingerprints: set[str] = set()
    for _ in range(5):
        with record("case") as rec:
            await count_tags({"b", "a", "c"})
        fingerprints.add(rec.trace.invocations[0].fingerprint or "")
    assert len(fingerprints) == 1  # identical fingerprint despite set ordering
    assert rec.trace.invocations[0].request["args"][0] == ["a", "b", "c"]


async def test_unsupported_argument_fails_with_clear_error() -> None:
    tools, _ = make_toolbox()

    @tools.tool()
    async def process(payload: Any) -> str:
        return "ok"

    class Mystery:
        pass

    calls: list[int] = []

    async def live() -> str:
        calls.append(1)
        return "ok"

    with record("case") as rec, pytest.raises(AdapterError, match="Mystery"):
        await process(payload=Mystery())
    assert calls == []
    assert rec.trace.invocations == []
    assert rec.trace.spans == []


async def test_tool_exception_is_recorded_and_reraised() -> None:
    tools, _ = make_toolbox()

    @tools.tool()
    async def failing() -> None:
        raise ValueError("tool failed")

    with record("case") as rec, pytest.raises(ValueError):
        await failing()
    (invocation,) = rec.trace.invocations
    assert invocation.response is None
    assert invocation.metadata["error"]["type"] == "ValueError"


async def test_replay_serves_recorded_output_without_calling_body() -> None:
    tools, registry = make_toolbox()

    @tools.tool(name="weather")
    async def weather(city: str) -> dict[str, Any]:
        return {"temperature": 21}

    with record("case") as rec:
        await weather("Berlin")
    fixture = build_envelope(rec.trace)

    # Replay with the real body replaced by a sentinel that must never run.
    real_calls: list[int] = []

    async def disabled(city: str) -> dict[str, Any]:
        real_calls.append(1)
        return {"temperature": -999}

    replayed = tools.wrap(disabled, name="weather")
    session_result: dict[str, Any] = {}
    from tracefork.replay import ReplaySession

    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        output = await replayed("Berlin")
    session_result["output"] = output

    assert output == {"temperature": 21}
    assert real_calls == []  # the actual function body never executed


async def test_replay_of_changed_arguments_fails_closed() -> None:
    tools, registry = make_toolbox()

    @tools.tool(name="weather")
    async def weather(city: str) -> dict[str, Any]:
        return {"temperature": 21}

    with record("case") as rec:
        await weather("Berlin")
    fixture = build_envelope(rec.trace)

    from tracefork.errors import ReplayMismatchError
    from tracefork.replay import ReplaySession

    real_calls: list[int] = []

    async def disabled(city: str) -> dict[str, Any]:
        real_calls.append(1)
        return {}

    replayed = tools.wrap(disabled, name="weather")
    session = ReplaySession(fixture=fixture, registry=registry)
    with session, pytest.raises(ReplayMismatchError):
        await replayed("Munich")
    assert real_calls == []
