"""Final coverage-closing tests: provider/transport edge branches."""

from datetime import UTC, datetime

import httpx
import pytest
from tracefork import record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.errors import AdapterError
from tracefork.models import Provenance, Trace
from tracefork.replay import ReplaySession
from tracefork.serialization import build_envelope
from tracefork_openai import instrument_openai
from typer.testing import CliRunner

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)

runner = CliRunner()


def _minimal_fixture() -> bytes:
    trace = Trace(trace_id="tr", name="minimal", started_at=T0, provenance=Provenance())
    return build_envelope(trace).to_json_bytes()


# --- CLI replay with a corrupt fixture file (replay.py 96-97) -----------------


def test_cli_replay_corrupt_fixture_exits_3(tmp_path) -> None:
    from tracefork_cli.main import app

    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    result = runner.invoke(app, ["replay", str(bad), "--entrypoint", "os:sep"])
    assert result.exit_code == 3
    assert "invalid fixture" in result.output


# --- suites entrypoint branches (suites.py 185-196) ----------------------------


def test_suite_entrypoint_without_colon(tmp_path) -> None:
    from tracefork_cli.suites import run_suite

    (tmp_path / "f.json").write_bytes(_minimal_fixture())
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "name: s\ncases:\n  - name: c\n    fixture: f.json\n    entrypoint: no-colon\n",
        encoding="utf-8",
    )
    result = run_suite(suite)
    assert result.cases[0].status == "invalid"
    assert "module:function" in result.cases[0].detail[0]


def test_suite_entrypoint_module_missing(tmp_path) -> None:
    from tracefork_cli.suites import run_suite

    (tmp_path / "f.json").write_bytes(_minimal_fixture())
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "name: s\ncases:\n"
        "  - name: c\n"
        "    fixture: f.json\n"
        "    entrypoint: totally_missing_module:run\n",
        encoding="utf-8",
    )
    result = run_suite(suite)
    assert result.cases[0].status == "invalid"
    assert "cannot load entrypoint" in result.cases[0].detail[0]


def test_suite_corrupt_fixture_file(tmp_path) -> None:
    from tracefork_cli.suites import run_suite

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    (fixtures_dir / "broken.json").write_text("{ not valid json", encoding="utf-8")
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "name: s\ncases:\n  - name: c\n    fixture: fixtures/broken.json\n    entrypoint: os:sep\n",
        encoding="utf-8",
    )
    result = run_suite(suite)
    assert result.cases[0].status == "invalid"
    assert "fixture unavailable" in result.cases[0].detail[0]


# --- openai adapter: sync client inside a running loop (125-126) ---------------


async def test_sync_openai_client_inside_running_loop_raises() -> None:
    from openai import OpenAI
    from tracefork.boundaries import BoundaryRuntime as RT

    registry = BoundaryRegistry()
    runtime = RT(registry=registry)
    sync_client = OpenAI(
        api_key="k",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        ),
    )
    instrument_openai(sync_client, runtime)
    with pytest.raises(AdapterError, match="running event loop"):
        sync_client.responses.create(model="gpt-test", input="hi")


# --- trace model: naive started_at (trace.py 52) --------------------------------


def test_trace_naive_started_at_coerced_to_utc() -> None:
    trace = Trace(
        trace_id="tr",
        name="naive",
        started_at=datetime(2026, 9, 2, 15, 0, 0),
        provenance=Provenance(),
    )
    assert trace.started_at.tzinfo is UTC


# --- httpx payload edge branches (167-168, 179, 181, 194) ----------------------


async def test_httpx_invalid_json_content_type_falls_back_to_text() -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, content=b"not really json", headers={"content-type": "application/json"}
        )

    from tracefork_httpx import TraceForkAsyncTransport

    client = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=httpx.MockTransport(handler), runtime=runtime)
    )

    async with client:
        with record("case") as rec:
            await client.get("https://api.example.test/x")
    (invocation,) = rec.trace.invocations
    assert invocation.response["content"] == "not really json"


async def test_httpx_replay_rebuilds_json_and_text_request_bodies() -> None:
    from tracefork_httpx import TraceForkAsyncTransport

    registry = BoundaryRegistry()
    record_runtime = BoundaryRuntime(registry=registry)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    recorder = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(
            inner=httpx.MockTransport(handler), runtime=record_runtime
        )
    )
    async with recorder:
        with record("bodies") as rec:
            await recorder.post(
                "https://api.example.test/with-json",
                json={"a": 1},
            )
            await recorder.post(
                "https://api.example.test/with-text",
                content="plain text",
                headers={"content-type": "text/plain"},
            )
            await recorder.post(
                "https://api.example.test/empty",
            )
    fixture = build_envelope(rec.trace)
    assert len(rec.trace.invocations) == 3

    dead = DeadTransportForTest()
    replay_runtime = BoundaryRuntime(registry=registry)
    offline = httpx.AsyncClient(
        transport=TraceForkAsyncTransport(inner=dead, runtime=replay_runtime)
    )
    session = ReplaySession(fixture=fixture, registry=registry)
    async with offline:
        with session:
            await offline.post("https://api.example.test/with-json", json={"a": 1})
            await offline.post(
                "https://api.example.test/with-text",
                content="plain text",
                headers={"content-type": "text/plain"},
            )
            await offline.post("https://api.example.test/empty")
    assert len(dead.requests) == 0
    assert session.result.matched == 3


class DeadTransportForTest(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise AssertionError("network touched")
