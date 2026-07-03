"""Evaluation suites (TF-150..153) with machine-readable output (TF-181..182).

A suite is a YAML file listing cases: a fixture, an entrypoint (module:function
async callable receiving the trace input), an optional replay policy and
declarative expectations. Cases run sequentially. Exit-code precedence:
3 invalid fixture/input, 2 execution error, 1 regression, 0 success.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from tracefork.assertions import (
    ResourceMaximums,
    ToolExpectations,
    TraceExpectations,
    evaluate_expectations,
)
from tracefork.bootstrap import registry as bootstrap_registry
from tracefork.boundaries import ReplayMode, ReplayPolicy
from tracefork.errors import FixtureError, ReplayError
from tracefork.replay import ReplaySession
from tracefork.serialization import parse_envelope


class CaseReplay(BaseModel):
    """Replay policy section of a suite case."""

    model_config = ConfigDict(extra="forbid")

    default: ReplayMode = ReplayMode.REPLAY
    llm: ReplayMode | None = None
    http: ReplayMode | None = None
    tools: dict[str, ReplayMode] = Field(default_factory=dict)

    def to_policy(self) -> ReplayPolicy:
        return ReplayPolicy(
            default=self.default, llm=self.llm, http=self.http, tools=dict(self.tools)
        )


class ExpectationsSpec(BaseModel):
    """Expectations section of a suite case (mirrors TraceExpectations)."""

    model_config = ConfigDict(extra="forbid")

    tools: ToolExpectations = Field(default_factory=ToolExpectations)
    before: list[tuple[str, str]] = Field(default_factory=list)
    max_calls: dict[str, int] = Field(default_factory=dict)
    max: ResourceMaximums = Field(default_factory=ResourceMaximums)

    def to_expectations(self) -> TraceExpectations:
        return TraceExpectations(
            tools=self.tools, before=self.before, max_calls=self.max_calls, max=self.max
        )


class SuiteCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    fixture: Path
    entrypoint: str
    replay: CaseReplay = Field(default_factory=CaseReplay)
    expect: ExpectationsSpec = Field(default_factory=ExpectationsSpec)


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    cases: list[SuiteCase] = Field(default_factory=list)


@dataclass
class CaseResult:
    name: str
    status: str  # "passed" | "failed" | "error" | "invalid"
    detail: list[str] = field(default_factory=list)


@dataclass
class SuiteResult:
    suite: str
    cases: list[CaseResult]

    @property
    def exit_code(self) -> int:
        statuses = {case.status for case in self.cases}
        if "invalid" in statuses:
            return 3
        if "error" in statuses:
            return 2
        if "failed" in statuses:
            return 1
        return 0

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for case in self.cases if case.status == "failed")


def load_suite(path: Path) -> Suite:
    """Load and validate a suite YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"suite file {path} must contain a YAML mapping"
        raise ValueError(msg)
    return Suite.model_validate(raw)


def run_suite(suite_path: Path) -> SuiteResult:
    """Run every case sequentially (TF-151) and collect evidence."""
    suite = load_suite(suite_path)
    root = suite_path.parent
    results = [_run_case(case, root) for case in suite.cases]
    return SuiteResult(suite=suite.name, cases=results)


def _run_case(case: SuiteCase, root: Path) -> CaseResult:
    fixture_path = case.fixture if case.fixture.is_absolute() else root / case.fixture
    try:
        envelope = parse_envelope(fixture_path.read_bytes())
    except (OSError, FixtureError) as exc:
        return CaseResult(case.name, "invalid", [f"fixture unavailable: {exc}"])
    try:
        entry = _load_entrypoint(case.entrypoint)
    except ValueError as exc:
        return CaseResult(case.name, "invalid", [str(exc)])

    # Entrypoint modules register boundary handlers on the bootstrap registry
    # at import time; the session must use that same registry for restore.
    session = ReplaySession(
        fixture=envelope, policy=case.replay.to_policy(), registry=bootstrap_registry()
    )

    async def execute() -> tuple[str, list[str]]:
        with session:
            try:
                await entry(envelope.trace.input)
            except ReplayError as exc:
                return "failed", [f"replay mismatch: {str(exc).splitlines()[0]}"]
            except Exception as exc:
                return "error", [f"{type(exc).__name__}: {exc}"]
        return "", []

    status, details = asyncio.run(execute())
    if status:
        return CaseResult(case.name, status, details)

    assertion_results = evaluate_expectations(session.result.trace, case.expect.to_expectations())
    failures = [f"{r.name}: {r.detail}" for r in assertion_results if not r.passed]
    return CaseResult(case.name, "failed" if failures else "passed", failures)


def _load_entrypoint(spec: str) -> Any:
    if ":" not in spec:
        msg = f"entrypoint must be module:function, got {spec!r}"
        raise ValueError(msg)
    module_name, function_name = spec.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        entry = getattr(module, function_name)
    except (ImportError, AttributeError) as exc:
        msg = f"cannot load entrypoint {spec!r}: {exc}"
        raise ValueError(msg) from exc
    if not callable(entry):
        msg = f"entrypoint {spec!r} is not callable"
        raise ValueError(msg)
    return entry


def format_text(result: SuiteResult) -> str:
    lines = [result.suite, "", f"{len(result.cases)} case(s)", ""]
    for case in result.cases:
        marker = "PASS" if case.status == "passed" else case.status.upper()
        lines.append(f"  {marker} {case.name}")
    lines.append("")
    lines.append(f"{result.passed} passed")
    lines.append(f"{result.failed} failed")
    failures = [case for case in result.cases if case.status != "passed"]
    if failures:
        lines.append("")
        lines.append("Failures:")
        for case in failures:
            lines.append("")
            lines.append(case.name)
            lines.extend(f"  {detail}" for detail in case.detail)
    return "\n".join(lines)


def format_json(result: SuiteResult) -> str:
    payload = {
        "suite": result.suite,
        "cases": [
            {"name": case.name, "status": case.status, "detail": case.detail}
            for case in result.cases
        ],
        "passed": result.passed,
        "failed": result.failed,
        "exit_code": result.exit_code,
    }
    return json.dumps(payload, indent=2)


def format_junit(result: SuiteResult) -> str:
    failures = sum(1 for case in result.cases if case.status == "failed")
    errors = sum(1 for case in result.cases if case.status == "error")
    suite_element = ET.Element(
        "testsuite",
        {
            "name": result.suite,
            "tests": str(len(result.cases)),
            "failures": str(failures),
            "errors": str(errors),
        },
    )
    for case in result.cases:
        case_element = ET.SubElement(suite_element, "testcase", {"name": case.name})
        if case.status == "failed":
            failure = ET.SubElement(case_element, "failure", {"message": case.name})
            failure.text = "\n".join(case.detail)
        elif case.status == "error":
            error = ET.SubElement(case_element, "error", {"message": case.name})
            error.text = "\n".join(case.detail)
        elif case.status == "invalid":
            error = ET.SubElement(case_element, "error", {"type": "invalid", "message": case.name})
            error.text = "\n".join(case.detail)
    return ET.tostring(suite_element, encoding="unicode", xml_declaration=False)


__all__ = [
    "CaseResult",
    "Suite",
    "SuiteCase",
    "SuiteResult",
    "format_json",
    "format_junit",
    "format_text",
    "load_suite",
    "run_suite",
]
