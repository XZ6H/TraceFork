"""Replay invariants (plan §34).

These tests encode the properties that make a green hermetic replay
meaningful. They are the contract the replay engine must never break.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from tracefork import record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.canonicalization import canonical_json
from tracefork.errors import ReplayMismatchError
from tracefork.models import Trace
from tracefork.replay import ReplaySession
from tracefork.serialization import FixtureEnvelope, build_envelope

from tests.conftest import EchoHandler

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_registry() -> BoundaryRegistry:
    registry = BoundaryRegistry()
    registry.register("tool.echo", EchoHandler())
    return registry


def live_never(calls: list[int]) -> Any:
    async def live() -> Any:
        calls.append(1)
        return "should never be returned"

    return live


async def record_fixture() -> FixtureEnvelope:
    runtime = BoundaryRuntime(registry=make_registry())
    with record("invariant-case") as rec:
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never([]))
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never([]))
        await runtime.invoke("tool.echo", "other", {"q": 1}, live_never([]))
    return build_envelope(rec.trace, created_at=T0)


def signature(trace: Trace) -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str, int]]]:
    """Timestamp- and ID-free execution signature of a trace."""
    spans = [
        (span.kind.value, span.name, span.status.value, canonical_json(span.output))
        for span in trace.spans
    ]
    invocations = [
        (invocation.name, invocation.fingerprint or "", invocation.occurrence)
        for invocation in trace.invocations
    ]
    return spans, invocations


async def test_invariant_1_hermetic_replay_makes_zero_external_calls() -> None:
    fixture = await record_fixture()
    runtime = BoundaryRuntime(registry=make_registry())
    calls: list[int] = []

    session = ReplaySession(fixture=fixture, registry=make_registry())
    with session:
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never(calls))
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never(calls))
        await runtime.invoke("tool.echo", "other", {"q": 1}, live_never(calls))

    assert calls == []  # the live implementation never executed
    assert session.result.is_hermetic is True
    assert session.result.network_calls == 0


async def test_invariant_2_every_replay_matches_one_recorded_invocation() -> None:
    fixture = await record_fixture()
    runtime = BoundaryRuntime(registry=make_registry())

    session = ReplaySession(fixture=fixture, registry=make_registry())
    with session:
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never([]))
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never([]))
        await runtime.invoke("tool.echo", "other", {"q": 1}, live_never([]))

    result = session.result
    assert result.matched == 3
    assert len(result.trace.invocations) == 3
    # Each replayed invocation corresponds to exactly one recorded one,
    # preserving fingerprint and occurrence.
    assert sorted((i.fingerprint, i.occurrence) for i in result.trace.invocations) == sorted(
        (i.fingerprint, i.occurrence) for i in fixture.trace.invocations
    )


async def test_invariant_3_same_fixture_and_code_replay_identically() -> None:
    fixture = await record_fixture()
    runtime = BoundaryRuntime(registry=make_registry())

    first_session = ReplaySession(fixture=fixture, registry=make_registry())
    with first_session:
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never([]))
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never([]))
        await runtime.invoke("tool.echo", "other", {"q": 1}, live_never([]))

    second_session = ReplaySession(fixture=fixture, registry=make_registry())
    with second_session:
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never([]))
        await runtime.invoke("tool.echo", "search", {"customer_id": 912}, live_never([]))
        await runtime.invoke("tool.echo", "other", {"q": 1}, live_never([]))

    assert signature(first_session.result.trace) == signature(second_session.result.trace)
    assert first_session.result.matched == second_session.result.matched


async def test_invariant_4_replay_never_mutates_the_fixture() -> None:
    fixture = await record_fixture()
    before = fixture.to_json_bytes()
    runtime = BoundaryRuntime(registry=make_registry())

    session = ReplaySession(fixture=fixture, registry=make_registry())
    with session, pytest.raises(ReplayMismatchError):
        await runtime.invoke("tool.echo", "search", {"customer_id": 404}, live_never([]))

    assert fixture.to_json_bytes() == before


async def test_invariant_5_mismatch_never_falls_back_to_live() -> None:
    fixture = await record_fixture()
    runtime = BoundaryRuntime(registry=make_registry())
    calls: list[int] = []

    session = ReplaySession(fixture=fixture, registry=make_registry())
    with session, pytest.raises(ReplayMismatchError):
        await runtime.invoke("tool.echo", "search", {"customer_id": 404}, live_never(calls))

    assert calls == []
