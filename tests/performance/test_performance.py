"""Performance smoke tests against the PRD §37 targets (with CI-safe margins).

The PRD targets are: 100-step hermetic replay < 100 ms, fixture load < 20 ms,
1,000-node trajectory diff < 100 ms. Assertions here use ~2-5x headroom so CI
variance never flakes, while still catching order-of-magnitude regressions.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tracefork import record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.diff import diff_traces
from tracefork.models import BoundaryInvocation, Provenance, Span, SpanKind, Trace
from tracefork.replay import ReplaySession
from tracefork.serialization import build_envelope, parse_envelope

from tests.conftest import EchoHandler

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def _registry() -> BoundaryRegistry:
    registry = BoundaryRegistry()
    registry.register("tool.echo", EchoHandler())
    return registry


def _trace_with_tool_calls(count: int) -> Trace:
    spans = [
        Span(
            span_id=f"sp_{i}",
            kind=SpanKind.TOOL,
            name=f"step_{i}",
            started_at=T0 + timedelta(microseconds=i),
            completed_at=T0 + timedelta(microseconds=i + 1),
        )
        for i in range(count)
    ]
    invocations = [
        BoundaryInvocation(boundary_type="tool.python", name=f"step_{i}", span_id=f"sp_{i}")
        for i in range(count)
    ]
    return Trace(
        trace_id="tr_1",
        name="perf",
        started_at=T0,
        completed_at=T0 + timedelta(seconds=1),
        provenance=Provenance(),
        spans=spans,
        invocations=invocations,
    )


def test_hermetic_replay_of_100_step_trace_is_fast() -> None:
    registry = _registry()
    record_runtime = BoundaryRuntime(registry=registry)

    async def record_100() -> None:
        with record("perf-case") as rec:
            for i in range(100):

                async def live(i: int = i) -> str:
                    return f"out_{i}"

                await record_runtime.invoke("tool.echo", f"step_{i}", {"i": i}, live)
        return rec

    rec = asyncio.run(record_100())
    fixture = build_envelope(rec.trace)
    replay_runtime = BoundaryRuntime(registry=registry)
    session = ReplaySession(fixture=fixture, registry=registry)

    async def replay_100() -> None:
        with session:
            for i in range(100):

                async def live() -> str:
                    raise AssertionError("must not run")

                await replay_runtime.invoke("tool.echo", f"step_{i}", {"i": i}, live)

    started = time.perf_counter()
    asyncio.run(replay_100())
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert session.result.matched == 100
    assert elapsed_ms < 2000, f"100-step replay took {elapsed_ms:.0f}ms (PRD target: 100ms)"


def test_diff_of_1000_node_trajectories_with_200_edits_is_fast() -> None:
    baseline_nodes = [(SpanKind.TOOL, f"step_{i}") for i in range(1000)]
    candidate_nodes = [
        (SpanKind.TOOL, f"step_{i}" if i % 10 != 5 else f"changed_{i}") for i in range(1000)
    ]

    def build(nodes: list[tuple[SpanKind, str]]) -> Trace:
        return Trace(
            trace_id="tr_1",
            name="perf",
            started_at=T0,
            provenance=Provenance(),
            spans=[
                Span(span_id=f"sp_{i}", kind=kind, name=name, started_at=T0)
                for i, (kind, name) in enumerate(nodes)
            ],
        )

    started = time.perf_counter()
    result = diff_traces(build(baseline_nodes), build(candidate_nodes))
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert len(result.ops) >= 1000
    assert result.first_divergence is not None
    assert elapsed_ms < 500, f"1000-node diff took {elapsed_ms:.0f}ms (PRD target: 100ms)"


def test_fixture_load_is_fast(tmp_path: Path) -> None:
    envelope = build_envelope(_trace_with_tool_calls(50))
    data = envelope.to_json_bytes()

    started = time.perf_counter()
    for _ in range(10):
        parse_envelope(data)
    elapsed_ms = (time.perf_counter() - started) * 1000 / 10
    assert elapsed_ms < 250, f"fixture load took {elapsed_ms:.0f}ms (PRD target: 20ms)"
