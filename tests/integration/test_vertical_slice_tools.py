"""Vertical slice 1 (plan §41): record a tool call, replay it offline, fail closed.

Proves the full chain — recording, serialization, canonicalization,
fingerprinting, matching, replay and fail-closed semantics — with a real
filesystem fixture store and no LLM integration.
"""

from typing import Any

import pytest
from tracefork import record
from tracefork.adapters import ToolBox
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.errors import ReplayMismatchError
from tracefork.replay import ReplaySession
from tracefork.serialization import FixtureEnvelope, build_envelope
from tracefork.storage import FilesystemFixtureStore


async def test_record_replay_fail_closed_slice(tmp_path: Any) -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    tools = ToolBox(runtime)
    store = FilesystemFixtureStore(tmp_path)

    real_calls: list[str] = []

    @tools.tool(name="tests.vertical.weather")
    async def weather(city: str) -> dict[str, Any]:
        real_calls.append(city)
        return {"temperature": 21}

    # 1. Record against the real function and persist the fixture.
    with record("weather-case") as rec:
        await weather("Berlin")
    assert real_calls == ["Berlin"]
    store.save("weather-case", build_envelope(rec.trace))

    # 2. Disable the real function; replay must serve the recording.
    async def disabled_weather(city: str) -> dict[str, Any]:
        raise AssertionError("the real function must never run during replay")

    offline = tools.wrap(disabled_weather, name="tests.vertical.weather")
    fixture: FixtureEnvelope = store.load("weather-case")
    replay_calls: list[str] = []
    session = ReplaySession(fixture=fixture, registry=registry)
    with session:
        output = await offline("Berlin")
        replay_calls.append("ran")
    assert output == {"temperature": 21}
    assert real_calls == ["Berlin"]  # unchanged: no new execution
    assert replay_calls == ["ran"]
    assert session.result.is_hermetic is True
    assert session.result.matched == 1

    # 3. Change the world: a different city has no recording.
    session2 = ReplaySession(fixture=fixture, registry=registry)
    with session2, pytest.raises(ReplayMismatchError):
        await offline("Munich")
