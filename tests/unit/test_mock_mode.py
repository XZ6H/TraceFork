"""Item 6b: MOCK mode — user-supplied responses, fail-closed when unconfigured."""

from typing import Any

import pytest
from tracefork import record
from tracefork.boundaries import (
    BoundaryRegistry,
    BoundaryRuntime,
    ReplayMode,
    ReplayPolicy,
)
from tracefork.errors import ReplayPolicyError
from tracefork.models import SpanKind
from tracefork.replay import ReplaySession
from tracefork.serialization import FixtureEnvelope, build_envelope

from tests.conftest import EchoHandler

T0_LIKE = None  # fixtures built live below


def _registry() -> BoundaryRegistry:
    registry = BoundaryRegistry()
    registry.register("tool.echo", EchoHandler())
    registry.register("llm.demo", EchoHandler())
    registry.register("llm.something", EchoHandler())
    return registry


def make_fixture() -> FixtureEnvelope:
    with record("mock-case", capture_provenance=False) as rec:
        pass
    return build_envelope(rec.trace)


def _counting_live(calls: list[int], result: Any = "live-result") -> Any:
    async def live() -> Any:
        calls.append(1)
        return result

    return live


async def test_mock_returns_payload_without_live_call() -> None:
    registry = _registry()
    runtime = BoundaryRuntime(registry=registry)
    policy = ReplayPolicy(
        tools={"search_orders": ReplayMode.MOCK},
        mocks={"search_orders": {"orders": [1, 2]}},
    )
    calls: list[int] = []

    session = ReplaySession(fixture=make_fixture(), registry=registry, policy=policy)
    with session:
        result = await runtime.invoke(
            "tool.echo", "search_orders", {"customer_id": 1}, _counting_live(calls)
        )

    assert result == {"orders": [1, 2]}
    assert calls == []  # the live implementation never ran
    assert session.result.mocked == 1
    (span,) = session.result.trace.spans
    assert span.attributes["mock"] is True
    (invocation,) = session.result.trace.invocations
    assert invocation.response == {"orders": [1, 2]}
    assert invocation.metadata["mock"] is True


async def test_mock_without_configured_payload_fails_closed() -> None:
    registry = _registry()
    runtime = BoundaryRuntime(registry=registry)
    policy = ReplayPolicy(tools={"search_orders": ReplayMode.MOCK})  # no mocks entry
    calls: list[int] = []

    session = ReplaySession(fixture=make_fixture(), registry=registry, policy=policy)
    with session, pytest.raises(ReplayPolicyError, match="no mock response configured"):
        await runtime.invoke("tool.echo", "search_orders", {}, _counting_live(calls))
    assert calls == []
    assert session.result.trace.invocations == []
    assert session.result.trace.spans == []


async def test_mock_mode_skips_the_recording_matcher() -> None:
    """MOCK boundaries are served from policy, not from the fixture."""
    registry = _registry()
    runtime = BoundaryRuntime(registry=registry)
    policy = ReplayPolicy(
        default=ReplayMode.MOCK,
        mocks={"anything": {"mocked": True}},
    )

    session = ReplaySession(fixture=make_fixture(), registry=registry, policy=policy)
    with session:
        result = await runtime.invoke("llm.something", "anything", {"q": 1}, _counting_live([]))
    assert result == {"mocked": True}
    assert session.result.mocked == 1


async def test_mock_span_kind_follows_boundary_family() -> None:
    registry = _registry()
    runtime = BoundaryRuntime(registry=registry)
    policy = ReplayPolicy(default=ReplayMode.MOCK, mocks={"planner": {"plan": []}})

    session = ReplaySession(fixture=make_fixture(), registry=registry, policy=policy)
    with session:
        await runtime.invoke("llm.demo", "planner", {}, _counting_live([]))
    (span,) = session.result.trace.spans
    assert span.kind is SpanKind.LLM
