"""Targeted edge-case tests closing the coverage gaps.

Each test pins a specific branch: canonicalization variants, defensive
error paths, CLI error mapping, formatter branches, and registry guards.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from tracefork import ToolBox, record
from tracefork.assertions import ResourceMaximums, TraceExpectations, evaluate_expectations
from tracefork.boundaries import (
    BoundaryRegistry,
    BoundaryRuntime,
    ExecutionContext,
    ExecutionMode,
    ReplayMode,
    ReplayPolicy,
    execution_context,
)
from tracefork.canonicalization import canonical_json
from tracefork.diff import diff_traces
from tracefork.errors import (
    AdapterError,
    RecordingError,
    ReplayPolicyError,
)
from tracefork.models import (
    BoundaryInvocation,
    Provenance,
    Span,
    SpanKind,
    Trace,
)
from tracefork.replay.matcher import MatchMiss, ReplayMatcher, format_mismatch
from tracefork.replay.session import ReplaySession
from tracefork.serialization import FixtureEnvelope, build_envelope, parse_envelope
from typer.testing import CliRunner

from tests.conftest import EchoHandler

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)
runner = CliRunner()


# --- canonicalization variants (json.py 35/37/39) ---------------------------


def test_canonical_json_date_variant() -> None:
    assert canonical_json(date(2026, 9, 2)) == '"2026-09-02"'


def test_canonical_json_uuid_variant() -> None:
    assert canonical_json(UUID("12345678-1234-5678-1234-567812345678")) == (
        '"12345678-1234-5678-1234-567812345678"'
    )


def test_canonical_json_enum_variant() -> None:
    from enum import Enum

    class Color(Enum):
        RED = "red"

    assert canonical_json(Color.RED) == '"red"'


# --- registry guards (registry.py 19/20) ------------------------------------


def test_registry_rejects_empty_boundary_type() -> None:
    registry = BoundaryRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        registry.register("", EchoHandler())


# --- runtime defensive branches (runtime.py 116-117/138-139/141-142) --------


def make_fixture() -> FixtureEnvelope:
    with record("case", capture_provenance=False) as rec:
        pass
    return build_envelope(rec.trace)


def _registry() -> BoundaryRegistry:
    registry = BoundaryRegistry()
    registry.register("tool.echo", EchoHandler())
    return registry


def _counting_live(calls: list[int]) -> Any:
    async def live() -> Any:
        calls.append(1)
        return "x"

    return live


async def test_live_boundary_without_candidate_recording_fails() -> None:
    """REPLAY + LIVE policy but no candidate recording attached."""
    harness_registry = _registry()
    fixture = make_fixture()
    session = ReplaySession(
        fixture=fixture,
        registry=harness_registry,
        policy=ReplayPolicy(default=ReplayMode.LIVE),
    )
    context = ExecutionContext(
        mode=ExecutionMode.REPLAY, replay_session=session, policy=session.policy
    )
    runtime = BoundaryRuntime(registry=harness_registry)
    async with execution_context(context):
        with pytest.raises(RecordingError, match="candidate recording"):
            await runtime.invoke("tool.echo", "x", {}, _counting_live([]))


async def test_fault_mode_fails_closed() -> None:
    harness_registry = _registry()
    fixture = make_fixture()
    session = ReplaySession(
        fixture=fixture,
        registry=harness_registry,
        policy=ReplayPolicy(default=ReplayMode.FAULT),
    )
    context = ExecutionContext(
        mode=ExecutionMode.REPLAY, replay_session=session, policy=session.policy
    )
    runtime = BoundaryRuntime(registry=harness_registry)
    async with execution_context(context):
        with pytest.raises(ReplayPolicyError, match="not supported"):
            await runtime.invoke("tool.echo", "x", {}, _counting_live([]))


async def test_record_mode_without_recording_fails() -> None:
    context = ExecutionContext(mode=ExecutionMode.RECORD, recording=None)
    runtime = BoundaryRuntime(registry=_registry())
    async with execution_context(context):
        with pytest.raises(RecordingError, match="requires an active recording"):
            await runtime.invoke("tool.echo", "x", {}, _counting_live([]))


# --- matcher diagnostics (matcher.py 169-178) --------------------------------


def _miss_with(closest: BoundaryInvocation | None, name: str = "search") -> MatchMiss:
    return MatchMiss(
        boundary_type="tool.search",
        name=name,
        request={"customer_id": 912},
        fingerprint="f" * 64,
        parent_name=None,
        candidates=[closest] if closest else [],
        closest=closest,
        similarity=0.5 if closest else 0.0,
    )


def test_format_mismatch_name_differs_hint() -> None:
    closest = BoundaryInvocation(
        boundary_type="tool.other",
        name="other_search",
        request={"customer_id": 912},
        span_id="sp_x",
    )
    text = format_mismatch(_miss_with(closest))
    assert "boundary name differs" in text
    assert "tool.search.search" in text
    assert "tool.other.other_search" in text


def test_format_mismatch_without_any_candidates() -> None:
    matcher = ReplayMatcher([])
    miss = matcher.match(
        "tool.search",
        "search_orders",
        {"customer_id": 1},
        fingerprint="a" * 64,
        parent_name=None,
    )
    assert isinstance(miss, MatchMiss)
    text = format_mismatch(miss)
    assert "No unconsumed recorded interactions remain" in text


# --- session defensive path (session.py 162-164) ------------------------------


async def test_mock_with_non_canonicalizable_request_fails() -> None:
    registry = _registry()
    runtime = BoundaryRuntime(registry=registry)
    policy = ReplayPolicy(default=ReplayMode.MOCK, mocks={"x": {"ok": True}})

    class Mystery:
        pass

    session = ReplaySession(fixture=make_fixture(), registry=registry, policy=policy)
    with session, pytest.raises(AdapterError, match="canonicalizable"):
        await runtime.invoke("tool.echo", "x", {"k": Mystery()}, _counting_live([]))


# --- python_tools variants (64/67/82/121) -------------------------------------


async def test_tool_argument_date_naive_datetime_and_tuple() -> None:
    tools_box = ToolBox(BoundaryRuntime(registry=_registry()))
    captured: list[Any] = []

    @tools_box.tool(name="describe")
    async def describe(day: Any, moment: Any, pair: Any) -> int:
        captured.append((day, moment, pair))
        return 1

    with record("case") as rec:
        await describe(date(2026, 9, 2), datetime(2026, 9, 2, 15, 0), (1, 2))
    request = rec.trace.invocations[0].request
    assert request["args"][0] == "2026-09-02"
    assert request["args"][1] == "2026-09-02T15:00:00Z"
    assert request["args"][2] == [1, 2]


def test_tool_wrapper_exposes_func_and_default_boundary_type() -> None:
    registry = _registry()
    runtime = BoundaryRuntime(registry=registry)
    tools_box = ToolBox(runtime)

    def underlying() -> None:
        return None

    wrapped = tools_box.wrap(underlying)
    assert wrapped.func is underlying
    assert wrapped.boundary_type == "tool.python"


# --- assertions cost evidence (assertions.py 191) ------------------------------


def test_max_cost_without_calculator_passes_with_evidence() -> None:
    trace = Trace(trace_id="tr", name="c", started_at=T0, provenance=Provenance())
    expectations = TraceExpectations(max=ResourceMaximums(cost_usd=1.0))
    results = evaluate_expectations(trace, expectations)
    evidence = next(r for r in results if r.name == "max cost")
    assert evidence.passed
    assert "no cost data" in evidence.detail


# --- diff backtrack matches (diff.py 175-177) ----------------------------------


def test_diff_backtrack_matches_inside_edited_middle() -> None:
    def t(name: str) -> Span:
        return Span(span_id=f"sp_{name}", kind=SpanKind.TOOL, name=name, started_at=T0)

    baseline = Trace(
        trace_id="tr",
        name="b",
        started_at=T0,
        provenance=Provenance(),
        spans=[t("a"), t("X"), t("m1"), t("m2")],
    )
    candidate = Trace(
        trace_id="tr2",
        name="c",
        started_at=T0,
        provenance=Provenance(),
        spans=[t("a"), t("Y"), t("m1"), t("m2"), t("Z")],
    )
    result = diff_traces(baseline, candidate)
    labels = [(op.op, op.baseline.name if op.baseline else op.candidate.name) for op in result.ops]
    assert labels == [
        ("match", "a"),
        ("remove", "X"),
        ("insert", "Y"),
        ("match", "m1"),
        ("match", "m2"),
        ("insert", "Z"),
    ]


# --- replay session defensive path (session.py) ---------------------------------


async def test_replay_request_canonicalization_failure_fails_closed() -> None:
    registry = _registry()
    runtime = BoundaryRuntime(registry=registry)
    fixture = make_fixture()
    session = ReplaySession(fixture=fixture, registry=registry)

    class Mystery:
        pass

    async def live() -> Any:
        raise AssertionError("must not run")

    with session, pytest.raises(AdapterError, match="canonicalizable"):
        await runtime.invoke("tool.echo", "x", {"k": Mystery()}, live)


# --- filesystem branches (28/40-42/60) ------------------------------------------


def test_filesystem_store_root_property(tmp_path) -> None:
    from tracefork.storage import FilesystemFixtureStore

    store = FilesystemFixtureStore(tmp_path)
    assert store.root == tmp_path


def test_filesystem_store_list_when_root_missing(tmp_path) -> None:
    from tracefork.storage import FilesystemFixtureStore

    assert FilesystemFixtureStore(tmp_path / "does-not-exist").list() == []


def test_filesystem_save_failure_cleans_up_temp_file(tmp_path, monkeypatch) -> None:
    from tracefork.storage import FilesystemFixtureStore

    store = FilesystemFixtureStore(tmp_path)
    envelope = build_envelope(
        Trace(trace_id="tr", name="c", started_at=T0, provenance=Provenance())
    )

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        store.save("case", envelope)
    assert list(tmp_path.glob("*.tmp")) == []  # temp file removed


# --- integrity section helpers reuse --------------------------------------------


def test_parse_envelope_rejects_tampered_real_payload() -> None:
    envelope = build_envelope(
        Trace(trace_id="tr", name="integrity-case", started_at=T0, provenance=Provenance())
    )
    tampered = envelope.to_json_bytes().replace(b'"integrity-case"', b'"tampered"', 1)
    with pytest.raises(Exception, match="digest mismatch"):
        parse_envelope(tampered)
