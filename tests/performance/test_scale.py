"""Scale-envelope tests: 10x the PRD §37 targets, offline and deterministic.

The PRD targets (100-step replay, 1,000-node diff) are covered in
test_performance.py. These tests verify the engine at ten times that scale:
10,000-step hermetic replays, 10,000-node diffs with 2,000 edits, 10,000-event
buffered streams, 1,000 concurrent recordings, and 10,000-span fixture loads.
Bounds are generous (seconds, not milliseconds) but still catch blow-ups.

Concurrent recordings use capture_provenance=False: provenance capture spawns
git subprocesses per recording (correct production behavior for long agent
runs, and an intentional cost at this artificial scale).
"""

import asyncio
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from typing import Any

from tracefork import record, span
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.canonicalization import Canonicalizer
from tracefork.diff import diff_traces
from tracefork.metrics import extract_metrics
from tracefork.models import BoundaryInvocation, Provenance, Span, SpanKind, Trace
from tracefork.replay import ReplaySession
from tracefork.serialization import build_envelope
from tracefork.storage import FilesystemFixtureStore
from tracefork.trajectory import build_graph
from tracefork_openai import OpenAIHandler

from tests.conftest import EchoHandler

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)
STEPS = 10_000


def make_registry() -> BoundaryRegistry:
    registry = BoundaryRegistry()
    registry.register("tool.python", EchoHandler())
    registry.register("tool.echo", EchoHandler())
    return registry


def make_trace(steps: int) -> Trace:
    canonicalizer = Canonicalizer()
    spans = [
        Span(
            span_id=f"sp_{i}",
            kind=SpanKind.TOOL,
            name=f"step_{i}",
            started_at=T0 + timedelta(microseconds=i),
            completed_at=T0 + timedelta(microseconds=i + 1),
        )
        for i in range(steps)
    ]
    invocations = [
        BoundaryInvocation(
            boundary_type="tool.python",
            name=f"step_{i}",
            span_id=f"sp_{i}",
            response={"i": i},
            fingerprint=canonicalizer.fingerprint("tool.python", f"step_{i}", {"i": i}),
            occurrence=i,
        )
        for i in range(steps)
    ]
    return Trace(
        trace_id="tr_scale",
        name="scale",
        started_at=T0,
        completed_at=T0 + timedelta(seconds=steps / 1000),
        provenance=Provenance(),
        spans=spans,
        invocations=invocations,
    )


def test_replay_of_10_000_step_trace() -> None:
    fixture = build_envelope(make_trace(STEPS))
    registry = make_registry()
    runtime = BoundaryRuntime(registry=registry)
    session = ReplaySession(fixture=fixture, registry=registry)

    async def replay_all() -> None:
        with session:
            for i in range(STEPS):

                async def live(i: int = i) -> Any:
                    raise AssertionError("must not run")

                await runtime.invoke("tool.python", f"step_{i}", {"i": i}, live)

    started = time.perf_counter()
    asyncio.run(replay_all())
    elapsed = time.perf_counter() - started
    assert session.result.matched == STEPS
    assert session.result.is_hermetic
    assert elapsed < 10, f"10,000-step replay took {elapsed:.1f}s"


def test_diff_of_10_000_nodes_with_2_000_edits() -> None:
    baseline = make_trace(STEPS)
    changed_spans = [
        span.model_copy(update={"name": f"changed_{i}"}) if i % 5 == 0 else span
        for i, span in enumerate(baseline.spans)
    ]
    changed = baseline.model_copy(update={"spans": changed_spans})

    started = time.perf_counter()
    result = diff_traces(baseline, changed)
    elapsed = time.perf_counter() - started
    assert result.first_divergence is not None
    assert elapsed < 10, f"10,000-node diff took {elapsed:.1f}s"


def test_stream_replay_with_10_000_events() -> None:
    events = [
        {
            "type": "response.output_text.delta",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": f"chunk {i}; ",
            "logprobs": None,
            "sequence_number": i + 1,
            "obfuscation": None,
        }
        for i in range(STEPS)
    ]
    events.append(
        {
            "type": "response.completed",
            "sequence_number": STEPS + 1,
            "response": {
                "id": "resp_scale",
                "object": "response",
                "created_at": 1756818000,
                "status": "completed",
                "model": "gpt-test",
                "output": [],
                "usage": {"input_tokens": 10, "output_tokens": STEPS, "total_tokens": STEPS + 10},
            },
        }
    )
    trace = Trace(
        trace_id="tr_stream",
        name="stream-scale",
        started_at=T0,
        provenance=Provenance(),
        spans=[Span(span_id="sp_1", kind=SpanKind.LLM, name="stream", started_at=T0)],
        invocations=[
            BoundaryInvocation(
                boundary_type="llm.openai",
                name="stream-model",
                span_id="sp_1",
                response={"__stream__": True, "events": events},
                metadata={"stream": True},
                fingerprint=Canonicalizer().fingerprint(
                    "llm.openai", "stream-model", {"input": "x"}
                ),
            )
        ],
    )
    fixture = build_envelope(trace)

    registry = BoundaryRegistry()
    registry.register("llm.openai", OpenAIHandler())
    runtime = BoundaryRuntime(registry=registry)
    session = ReplaySession(fixture=fixture, registry=registry)

    async def replay() -> int:
        with session:

            async def live() -> Any:
                raise AssertionError("must not run")

            stream = await runtime.invoke("llm.openai", "stream-model", {"input": "x"}, live)
            return len([event async for event in stream])

    tracemalloc.start()
    started = time.perf_counter()
    consumed = asyncio.run(replay())
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert consumed == STEPS + 1
    assert elapsed < 30, f"10,000-event stream replay took {elapsed:.1f}s"
    # 30s bound: ~12s on the slowest CI runner, ~2s locally — catches
    # blow-ups while tolerating runner variance.
    assert peak_bytes < 512 * 1024 * 1024, f"peak memory {peak_bytes // 1024 // 1024}MB"


def test_1_000_concurrent_recordings_stay_isolated() -> None:
    """1,000 parallel tasks, each with its own recording: full isolation."""
    registry = make_registry()
    runtime = BoundaryRuntime(registry=registry)

    async def one(n: int) -> tuple[int, int, str]:
        with (
            record(f"case-{n}", capture_provenance=False) as rec,
            span(f"outer-{n}", kind=SpanKind.AGENT),
        ):

            async def live() -> str:
                return f"out-{n}"

            await runtime.invoke("tool.echo", f"tool_{n}", {"n": n}, live)
        return len(rec.trace.spans), len(rec.trace.invocations), rec.trace.name

    async def run_all() -> list[tuple[int, int, str]]:
        return await asyncio.gather(*(one(n) for n in range(1_000)))

    started = time.perf_counter()
    results = asyncio.run(run_all())
    elapsed = time.perf_counter() - started

    assert all(spans == 2 and invocations == 1 for spans, invocations, _ in results)
    assert all(name == f"case-{n}" for n, (_, _, name) in enumerate(results))
    assert elapsed < 15, f"1,000 concurrent recordings took {elapsed:.1f}s"


def test_fixture_load_of_10_000_spans(tmp_path) -> None:
    fixture = build_envelope(make_trace(STEPS))
    store = FilesystemFixtureStore(tmp_path)
    store.save("scale", fixture)

    started = time.perf_counter()
    loaded = store.load("scale")
    graph = build_graph(loaded.trace)
    metrics = extract_metrics(loaded.trace)
    elapsed = time.perf_counter() - started

    assert len(graph.nodes) == STEPS
    assert metrics.tool_calls == STEPS
    assert elapsed < 10, f"10,000-span fixture load took {elapsed:.1f}s"
