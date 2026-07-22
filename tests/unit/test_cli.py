"""TF-100..104: CLI — init, record, inspect, replay, error mapping, exit codes."""

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


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _record_weather_fixture(tmp_path: Path) -> Path:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    tools = ToolBox(runtime)

    @tools.tool(name="weather")
    async def weather(city: str) -> dict[str, Any]:
        return {"temperature": 21}

    import asyncio

    with record("weather-case", input={"city": "Berlin"}) as rec:
        asyncio.run(weather("Berlin"))
    store = FilesystemFixtureStore(tmp_path)
    store.save("weather-case", build_envelope(rec.trace))
    return tmp_path / "weather-case.json"


def test_init_creates_project_layout(workspace: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (workspace / ".tracefork" / "fixtures").is_dir()
    assert (workspace / ".tracefork" / "runs").is_dir()
    assert (workspace / ".tracefork" / "config.yaml").exists()
    # Idempotent.
    assert runner.invoke(app, ["init"]).exit_code == 0


def test_inspect_summarizes_fixture(workspace: Path) -> None:
    fixture_path = _record_weather_fixture(workspace)
    result = runner.invoke(app, ["inspect", str(fixture_path)])
    assert result.exit_code == 0
    assert "weather-case" in result.output
    assert "tool: 1" in result.output


def test_inspect_invalid_fixture_exits_3(workspace: Path) -> None:
    bad = workspace / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["inspect", str(bad)])
    assert result.exit_code == 3


def test_replay_hermetic_passes_with_zero_live_calls(workspace: Path) -> None:
    fixture_path = _record_weather_fixture(workspace)
    (workspace / "cliagent.py").write_text(
        "from tracefork.bootstrap import tools\n"
        "\n"
        "@tools.tool(name='weather')\n"
        "async def weather(city):\n"
        "    return {'temperature': 25}\n"
        "\n"
        "async def run(input_data):\n"
        "    return await weather(input_data['city'])\n",
        encoding="utf-8",
    )
    import sys

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.syspath_prepend(str(workspace))
    try:
        result = runner.invoke(app, ["replay", str(fixture_path), "--entrypoint", "cliagent:run"])
    finally:
        monkeypatch.undo()
        sys.modules.pop("cliagent", None)

    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    assert "Tool calls replayed:  1" in result.output
    assert "Live calls:           0" in result.output


def test_replay_mismatch_fails_with_exit_1(workspace: Path) -> None:
    fixture_path = _record_weather_fixture(workspace)
    (workspace / "cliagent_bad.py").write_text(
        "from tracefork.bootstrap import tools\n"
        "\n"
        "@tools.tool(name='weather')\n"
        "async def weather(city):\n"
        "    return {'temperature': 25}\n"
        "\n"
        "async def run(input_data):\n"
        "    return await weather('Munich')\n",
        encoding="utf-8",
    )
    import sys

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.syspath_prepend(str(workspace))
    try:
        result = runner.invoke(
            app, ["replay", str(fixture_path), "--entrypoint", "cliagent_bad:run"]
        )
    finally:
        monkeypatch.undo()
        sys.modules.pop("cliagent_bad", None)

    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output
    assert "Munich" in result.output


def test_replay_live_tools_flag_runs_live(workspace: Path) -> None:
    fixture_path = _record_weather_fixture(workspace)
    (workspace / "cliagent_live.py").write_text(
        "from tracefork.bootstrap import tools\n"
        "\n"
        "@tools.tool(name='weather')\n"
        "async def weather(city):\n"
        "    return {'temperature': 25}\n"
        "\n"
        "async def run(input_data):\n"
        "    return await weather(input_data['city'])\n",
        encoding="utf-8",
    )
    import sys

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.syspath_prepend(str(workspace))
    try:
        result = runner.invoke(
            app,
            ["replay", str(fixture_path), "--entrypoint", "cliagent_live:run", "--live", "tools"],
        )
    finally:
        monkeypatch.undo()
        sys.modules.pop("cliagent_live", None)

    assert result.exit_code == 0, result.output
    assert "Live calls:           1" in result.output
    assert "LIVE tool.python.weather" in result.output


def test_record_subprocess_writes_fixture(workspace: Path) -> None:
    script = workspace / "demo_script.py"
    script.write_text(
        "import asyncio\n"
        "from tracefork.bootstrap import tools\n"
        "\n"
        "@tools.tool()\n"
        "async def greet(name):\n"
        "    return f'hi {name}'\n"
        "\n"
        "asyncio.run(greet('world'))\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["record", "--name", "demo", "python", str(script)])
    assert result.exit_code == 0, result.output

    fixture_path = workspace / ".tracefork" / "fixtures" / "demo.json"
    assert fixture_path.exists()
    envelope = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert envelope["fixture_version"] == "1"
    assert len(envelope["trace"]["invocations"]) == 1


def test_record_missing_script_exits_3(workspace: Path) -> None:
    result = runner.invoke(app, ["record", "python", "nope.py"])
    assert result.exit_code == 3


def test_replay_bad_entrypoint_exits_3(workspace: Path) -> None:
    fixture_path = _record_weather_fixture(workspace)
    result = runner.invoke(app, ["replay", str(fixture_path), "--entrypoint", "not-a-module:run"])
    assert result.exit_code == 3


def test_diff_reports_trajectory_and_resources(workspace: Path) -> None:
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    tools = ToolBox(runtime)

    @tools.tool(name="weather")
    async def weather(city: str) -> dict[str, Any]:
        return {"temperature": 21}

    import asyncio

    store = FilesystemFixtureStore(workspace)
    with record("baseline", input={"city": "Berlin"}) as rec:
        asyncio.run(weather("Berlin"))
    store.save("baseline", build_envelope(rec.trace))

    with record("candidate", input={"city": "Berlin"}) as rec2:
        asyncio.run(weather("Berlin"))
        asyncio.run(weather("Berlin"))
    store.save("candidate", build_envelope(rec2.trace))

    result = runner.invoke(
        app,
        ["diff", str(workspace / "baseline.json"), str(workspace / "candidate.json")],
    )
    assert result.exit_code == 0, result.output
    assert "+ tool:weather" in result.output
    assert "First divergence" in result.output
    assert "tool_calls: 1 -> 2" in result.output


def test_record_failing_script_saves_failed_fixture(workspace: Path) -> None:
    script = workspace / "broken_script.py"
    script.write_text(
        "import asyncio\n"
        "from tracefork.bootstrap import tools\n"
        "\n"
        "@tools.tool()\n"
        "async def greet(name):\n"
        "    raise RuntimeError('tool exploded')\n"
        "\n"
        "asyncio.run(greet('world'))\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["record", "--name", "broken", "python", str(script)])
    assert result.exit_code == 2, result.output

    fixture_path = workspace / ".tracefork" / "fixtures" / "broken.json"
    assert fixture_path.exists()  # the failed execution is still recorded
    envelope = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert envelope["trace"]["status"] == "failed"
    assert envelope["trace"]["invocations"][0]["metadata"]["error"]["type"] == "RuntimeError"


def test_record_propagates_script_exit_code_and_saves_fixture(workspace: Path) -> None:
    script = workspace / "exit_script.py"
    script.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    result = runner.invoke(app, ["record", "--name", "exited", "python", str(script)])
    assert result.exit_code == 3
    assert (workspace / ".tracefork" / "fixtures" / "exited.json").exists()


def test_record_passes_script_arguments(workspace: Path) -> None:
    script = workspace / "args_script.py"
    script.write_text(
        "import sys\nassert sys.argv[1] == 'arg1' and sys.argv[2] == 'arg2', sys.argv\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["record", "python", str(script), "arg1", "arg2"])
    assert result.exit_code == 0, result.output


def test_replay_invalid_live_target_exits_3(workspace: Path) -> None:
    fixture_path = _record_weather_fixture(workspace)
    result = runner.invoke(
        app,
        ["replay", str(fixture_path), "--entrypoint", "cliagent:run", "--live", "nope"],
    )
    assert result.exit_code == 3
