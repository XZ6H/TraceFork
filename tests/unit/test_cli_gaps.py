"""CLI and eval-suite gap coverage: error mapping, formatters, edge branches."""

import asyncio
import sys
from typing import Any

import pytest
from tracefork import ToolBox, record
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.models import BoundaryInvocation, Provenance, Trace
from tracefork.serialization import FixtureEnvelope, build_envelope
from tracefork.storage import FilesystemFixtureStore
from tracefork_cli.main import app
from typer.testing import CliRunner

from tests.unit.test_coverage_edges import T0, make_fixture

runner = CliRunner()


def _record_weather_fixture(tmp_path) -> FixtureEnvelope:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    tools_box = ToolBox(runtime)

    @tools_box.tool(name="weather")
    async def weather(city: str) -> dict[str, Any]:
        return {"temperature": 21}

    with record("weather-case", input={"city": "Berlin"}) as rec:
        asyncio.run(weather("Berlin"))
    return build_envelope(rec.trace)


def test_cli_diff_invalid_fixture_exits_3(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["diff", str(bad), str(good)])
    assert result.exit_code == 3


def test_cli_diff_prints_removals(tmp_path) -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    tools_box = ToolBox(runtime)

    @tools_box.tool(name="escalate_case")
    async def escalate_case(city: str) -> dict[str, Any]:
        return {"escalated": True}

    @tools_box.tool(name="weather")
    async def weather(city: str) -> dict[str, Any]:
        return {"temperature": 21}

    store = FilesystemFixtureStore(tmp_path)
    with record("baseline", input={"city": "Berlin"}) as rec1:
        asyncio.run(escalate_case("Berlin"))
        asyncio.run(weather("Berlin"))
    store.save("baseline", build_envelope(rec1.trace))
    with record("candidate", input={"city": "Berlin"}) as rec2:
        asyncio.run(weather("Berlin"))
    store.save("candidate", build_envelope(rec2.trace))

    result = runner.invoke(
        app, ["diff", str(tmp_path / "baseline.json"), str(tmp_path / "candidate.json")]
    )
    assert result.exit_code == 0
    assert "- tool:escalate_case" in result.output


def test_cli_eval_invalid_format_exits_3(tmp_path) -> None:
    from tracefork_cli.main import app

    suite = tmp_path / "suite.yaml"
    suite.write_text("name: s\ncases: []\n", encoding="utf-8")
    result = runner.invoke(app, ["eval", str(suite), "--format", "nope"])
    assert result.exit_code == 3


def test_cli_inspect_prints_tokens(tmp_path) -> None:
    trace = Trace(trace_id="tr", name="token-case", started_at=T0, provenance=Provenance())
    trace.invocations.append(
        BoundaryInvocation(
            boundary_type="llm.openai",
            name="gpt-test",
            span_id="sp_1",
            metadata={"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}},
        )
    )
    fixture = tmp_path / "tokens.json"
    fixture.write_bytes(build_envelope(trace).to_json_bytes())
    result = runner.invoke(app, ["inspect", str(fixture)])
    assert result.exit_code == 0
    assert "tokens:   15" in result.output


def test_cli_record_without_script_exits_3() -> None:
    result = runner.invoke(app, ["record"])
    assert result.exit_code == 3


def test_cli_replay_entrypoint_without_colon_exits_3(tmp_path) -> None:
    fixture = tmp_path / "f.json"
    fixture.write_bytes(make_fixture().to_json_bytes())
    result = runner.invoke(app, ["replay", str(fixture), "--entrypoint", "no-colon"])
    assert result.exit_code == 3


def test_cli_replay_non_callable_entrypoint_exits_3(tmp_path) -> None:
    fixture = tmp_path / "f.json"
    fixture.write_bytes(make_fixture().to_json_bytes())
    result = runner.invoke(app, ["replay", str(fixture), "--entrypoint", "os:sep"])
    assert result.exit_code == 3


def test_cli_replay_execution_error_exits_2(tmp_path) -> None:
    fixture = tmp_path / "f.json"
    fixture.write_bytes(make_fixture().to_json_bytes())
    (tmp_path / "boom_mod.py").write_text(
        "async def run(i):\n    raise RuntimeError('kaboom')\n", encoding="utf-8"
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        result = runner.invoke(app, ["replay", str(fixture), "--entrypoint", "boom_mod:run"])
    finally:
        monkeypatch.undo()
        sys.modules.pop("boom_mod", None)
    assert result.exit_code == 2, result.output


def test_cli_version_flag() -> None:
    from tracefork_cli.main import app

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "tracefork" in result.output


def test_main_entrypoint_executes(monkeypatch) -> None:
    from tracefork_cli.main import main as cli_main

    monkeypatch.setattr(sys, "argv", ["tracefork", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        cli_main()
    assert excinfo.value.code == 0


def test_suite_replay_mismatch_reported(tmp_path) -> None:
    from tracefork_cli.suites import run_suite

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    tools_box = ToolBox(runtime)

    @tools_box.tool(name="weather")
    async def weather(city: str) -> dict[str, Any]:
        return {"temperature": 21}

    with record("weather-case", input={"city": "Berlin"}) as rec:
        asyncio.run(weather("Berlin"))
    FilesystemFixtureStore(fixtures_dir).save("weather-case", build_envelope(rec.trace))

    (tmp_path / "wrong_args_mod.py").write_text(
        "from tracefork.bootstrap import tools\n"
        "\n"
        "@tools.tool(name='weather')\n"
        "async def weather(city):\n"
        "    return {'temperature': 21}\n"
        "\n"
        "async def run(input_data):\n"
        "    return await weather('Munich')\n",
        encoding="utf-8",
    )
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "name: mismatch-suite\n"
        "cases:\n"
        "  - name: wrong-args\n"
        "    fixture: fixtures/weather-case.json\n"
        "    entrypoint: wrong_args_mod:run\n",
        encoding="utf-8",
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        result = run_suite(suite)
    finally:
        monkeypatch.undo()
        sys.modules.pop("wrong_args_mod", None)
    assert result.exit_code == 1
    assert "replay mismatch" in result.cases[0].detail[0]


def test_suite_replay_mock_serves_from_policy(tmp_path) -> None:
    from tracefork_cli.suites import run_suite

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    tools_box = ToolBox(runtime)

    @tools_box.tool(name="weather")
    async def weather(city: str) -> dict[str, Any]:
        return {"temperature": 21}

    with record("weather-case", input={"city": "Berlin"}) as rec:
        asyncio.run(weather("Berlin"))
    FilesystemFixtureStore(fixtures_dir).save("weather-case", build_envelope(rec.trace))

    (tmp_path / "mocked_mod.py").write_text(
        "from tracefork.bootstrap import tools\n"
        "\n"
        "@tools.tool(name='weather')\n"
        "async def weather(city):\n"
        "    return {'temperature': -999}\n"
        "\n"
        "async def run(input_data):\n"
        "    return await weather(input_data['city'])\n",
        encoding="utf-8",
    )
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "name: mock-suite\n"
        "cases:\n"
        "  - name: mocked\n"
        "    fixture: fixtures/weather-case.json\n"
        "    entrypoint: mocked_mod:run\n"
        "    replay:\n"
        "      tools:\n"
        "        weather: mock\n"
        "      mocks:\n"
        "        weather:\n"
        "          temperature: 42\n"
        "    expect:\n"
        "      tools:\n"
        "        require: [weather]\n",
        encoding="utf-8",
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        result = run_suite(suite)
    finally:
        monkeypatch.undo()
        sys.modules.pop("mocked_mod", None)
    assert result.exit_code == 0, result.cases[0].detail
    assert result.cases[0].status == "passed"


def test_suite_non_mapping_yaml_exits_3(tmp_path) -> None:
    from tracefork_cli.suites import load_suite

    suite = tmp_path / "list.yaml"
    suite.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        load_suite(suite)


def test_suite_entrypoint_not_callable(tmp_path) -> None:
    from tracefork_cli.suites import run_suite

    (tmp_path / "f.json").write_bytes(
        build_envelope(
            Trace(trace_id="tr", name="minimal", started_at=T0, provenance=Provenance())
        ).to_json_bytes()
    )
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "name: s\ncases:\n  - name: c\n    fixture: f.json\n    entrypoint: os:sep\n",
        encoding="utf-8",
    )
    result = run_suite(suite)
    assert result.cases[0].status == "invalid"


def test_junit_error_and_invalid_elements() -> None:
    import xml.etree.ElementTree as ET

    from tracefork_cli.suites import CaseResult, SuiteResult, format_junit

    result = SuiteResult(
        suite="mixed",
        cases=[
            CaseResult(name="crashed", status="error", detail=["RuntimeError: x"]),
            CaseResult(name="bad-fixture", status="invalid", detail=["missing"]),
        ],
    )
    parsed = ET.fromstring(format_junit(result))
    errors = parsed.findall("testcase/error")
    assert len(errors) == 2
