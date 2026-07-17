"""TF-150..153, TF-181..182: evaluation suites — YAML schema, runner, exit codes, formats."""

import json
from pathlib import Path
from typing import Any

import pytest
from tracefork import record
from tracefork.adapters import ToolBox
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.serialization import build_envelope
from tracefork.storage import FilesystemFixtureStore
from tracefork_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

AGENT_GOOD = """\
from tracefork.bootstrap import tools


@tools.tool(name="weather")
async def weather(city):
    return {"temperature": 21}


async def run(input_data):
    return await weather(input_data["city"])
"""

AGENT_BUGGY = """\
from tracefork.bootstrap import tools


@tools.tool(name="weather")
async def weather(city):
    return {"temperature": 21}


@tools.tool(name="escalate_case")
async def escalate_case(city):
    return {"escalated": True}


async def run(input_data):
    await escalate_case(input_data["city"])
    return await weather(input_data["city"])
"""

SUITE = """\
name: weather-regressions

cases:

  - name: berlin-passes
    fixture: fixtures/weather-case.json
    entrypoint: {module}:run
    expect:
      tools:
        require: [weather]
        forbid: [escalate_case]
      max:
        tool_calls: 2
"""

SUITE_STRICT_MAX = """\
name: weather-regressions

cases:

  - name: berlin-max-calls
    fixture: fixtures/weather-case.json
    entrypoint: {module}:run
    expect:
      max:
        tool_calls: 0
"""


def _make_incident_fixture(fixtures_dir: Path) -> None:
    """Record the production incident: escalate_case + weather('Berlin')."""
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    tools = ToolBox(runtime)

    @tools.tool(name="weather")
    async def weather(city: str) -> dict[str, Any]:
        return {"temperature": 21}

    @tools.tool(name="escalate_case")
    async def escalate_case(city: str) -> dict[str, Any]:
        return {"escalated": True}

    import asyncio

    with record("weather-case", input={"city": "Berlin"}) as rec:
        asyncio.run(escalate_case("Berlin"))
        asyncio.run(weather("Berlin"))
    store = FilesystemFixtureStore(fixtures_dir)
    store.save("weather-case", build_envelope(rec.trace))


def _write_suite(
    tmp_path: Path,
    template: str,
    module: str,
    agent_source: str,
) -> Path:
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    _make_incident_fixture(fixtures_dir)
    (tmp_path / f"{module}.py").write_text(agent_source, encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(template.format(module=module), encoding="utf-8")
    return suite_path


def _run_eval(suite_path: Path, extra: list[str] | None = None) -> Any:
    import sys

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.syspath_prepend(str(suite_path.parent))
    try:
        result = runner.invoke(app, ["eval", str(suite_path), *(extra or [])])
    finally:
        monkeypatch.undo()
        for name in list(sys.modules):
            if name.startswith("suite_agent_"):
                sys.modules.pop(name, None)
    return result


def test_suite_passing_case_exits_zero(tmp_path: Path) -> None:
    suite_path = _write_suite(tmp_path, SUITE, "suite_agent_good", AGENT_GOOD)
    result = _run_eval(suite_path)
    assert result.exit_code == 0, result.output
    assert "1 passed" in result.output
    assert "berlin-passes" in result.output


def test_suite_forbidden_tool_fails_with_exit_one(tmp_path: Path) -> None:
    suite_path = _write_suite(tmp_path, SUITE, "suite_agent_buggy", AGENT_BUGGY)
    result = _run_eval(suite_path)
    assert result.exit_code == 1, result.output
    assert "1 failed" in result.output
    assert "forbidden tool escalate_case" in result.output


def test_suite_max_calls_regression_fails(tmp_path: Path) -> None:
    suite_path = _write_suite(tmp_path, SUITE_STRICT_MAX, "suite_agent_max", AGENT_GOOD)
    result = _run_eval(suite_path)
    assert result.exit_code == 1, result.output
    assert "1 call(s), limit 0" in result.output


def test_suite_missing_fixture_exits_three(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    (tmp_path / "suite_agent_missing.py").write_text(AGENT_GOOD, encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(SUITE.format(module="suite_agent_missing"), encoding="utf-8")
    result = _run_eval(suite_path)
    assert result.exit_code == 3


def test_suite_entrypoint_crash_exits_two(tmp_path: Path) -> None:
    crashing = AGENT_GOOD + "\n\nasync def boom():\n    raise RuntimeError('x')\n"
    crashing_suite = SUITE.replace("entrypoint: {module}:run", "entrypoint: {module}:boom")
    suite_path = _write_suite(tmp_path, crashing_suite, "suite_agent_crash", crashing)
    result = _run_eval(suite_path)
    assert result.exit_code == 2, result.output


def test_suite_json_output(tmp_path: Path) -> None:
    suite_path = _write_suite(tmp_path, SUITE, "suite_agent_json", AGENT_GOOD)
    result = _run_eval(suite_path, ["--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["suite"] == "weather-regressions"
    assert payload["cases"][0]["name"] == "berlin-passes"
    assert payload["cases"][0]["status"] == "passed"
    assert payload["exit_code"] == 0


def test_suite_junit_output(tmp_path: Path) -> None:
    suite_path = _write_suite(tmp_path, SUITE, "suite_agent_junit", AGENT_GOOD)
    result = _run_eval(suite_path, ["--format", "junit"])
    assert result.exit_code == 0, result.output
    assert "<testsuite" in result.output
    assert "<testcase name=" in result.output


def test_suite_invalid_yaml_exits_three(tmp_path: Path) -> None:
    suite_path = tmp_path / "bad.yaml"
    suite_path.write_text("name: [unclosed", encoding="utf-8")
    result = _run_eval(suite_path)
    assert result.exit_code == 3


def test_suite_case_timeout_exits_two(tmp_path: Path) -> None:
    slow_agent = """\
import asyncio

from tracefork.bootstrap import tools


@tools.tool(name="weather")
async def weather(city):
    return {"temperature": 21}


async def run(input_data):
    await asyncio.sleep(5)
"""
    slow_suite = """\
name: timeout-suite

cases:

  - name: hangs
    fixture: fixtures/weather-case.json
    entrypoint: {module}:run
    timeout_seconds: 0.2
"""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    _make_incident_fixture(fixtures_dir)
    (tmp_path / "suite_agent_slow.py").write_text(slow_agent, encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(slow_suite.format(module="suite_agent_slow"), encoding="utf-8")
    result = _run_eval(suite_path)
    assert result.exit_code == 2, result.output
    assert "timed out" in result.output
