"""TF-170..173: redaction — secret keys, custom redactors, timing, security."""

import asyncio
import json
from typing import Any

from tracefork import record
from tracefork.adapters import ToolBox
from tracefork.boundaries import BoundaryRegistry, BoundaryRuntime
from tracefork.redaction import RedactionEngine, redactor
from tracefork.serialization import build_envelope


def _tool_fixture(payload: dict[str, Any], **record_kwargs: Any):
    registry = BoundaryRegistry()
    runtime = BoundaryRuntime(registry=registry)
    tools = ToolBox(runtime)

    @tools.tool(name="submit")
    async def submit(data: dict[str, Any]) -> dict[str, Any]:
        return {"received": data}

    async def run() -> None:
        with record("case", **record_kwargs) as rec:
            await submit(data=payload)
        return rec

    return asyncio.run(run())


def test_default_engine_redacts_secret_keys_case_insensitively() -> None:
    engine = RedactionEngine()
    redacted = engine.apply({"API_KEY": "sk-123", "Authorization": "Bearer x", "user": "bob"})
    assert redacted == {"API_KEY": "[REDACTED]", "Authorization": "[REDACTED]", "user": "bob"}


def test_default_engine_redacts_nested_values() -> None:
    engine = RedactionEngine()
    redacted = engine.apply(
        {"request": {"headers": {"cookie": "session=abc"}, "body": {"password": "hunter2"}}}
    )
    assert redacted == {
        "request": {"headers": {"cookie": "[REDACTED]"}, "body": {"password": "[REDACTED]"}}
    }


def test_engine_leaves_lists_and_scalars_intact() -> None:
    engine = RedactionEngine()
    assert engine.apply(["a", 1, True]) == ["a", 1, True]
    assert engine.apply("token") == "token"


def test_custom_redactor_runs_after_key_redaction() -> None:
    @redactor
    def scrub_customer(data: Any) -> Any:
        if isinstance(data, dict) and "customer_email" in data:
            data = {**data, "customer_email": "[SCRUBBED]"}
        return data

    engine = RedactionEngine(redactors=[scrub_customer])
    redacted = engine.apply({"customer_email": "a@b.c", "api_key": "sk"})
    assert redacted == {"customer_email": "[SCRUBBED]", "api_key": "[REDACTED]"}


def test_recording_redacts_invocation_request_and_response() -> None:
    rec = _tool_fixture({"api_key": "sk-123", "city": "Berlin"})
    (invocation,) = rec.trace.invocations
    assert invocation.request["kwargs"]["data"] == {
        "api_key": "[REDACTED]",
        "city": "Berlin",
    }
    assert invocation.response["received"]["api_key"] == "[REDACTED]"


def test_secret_never_reaches_fixture_bytes() -> None:
    rec = _tool_fixture({"api_key": "super-secret-value", "Authorization": "Bearer tok"})
    envelope = build_envelope(rec.trace, created_at=rec.trace.started_at)
    text = envelope.to_json_bytes().decode("utf-8")
    assert "super-secret-value" not in text
    assert "Bearer tok" not in text
    assert "[REDACTED]" in text


def test_span_input_is_redacted_before_persistence() -> None:
    rec = _tool_fixture({"password": "hunter2"})
    (span,) = rec.trace.spans
    assert span.input["kwargs"]["data"] == {"password": "[REDACTED]"}


def test_extra_keys_via_record_parameter() -> None:
    rec = _tool_fixture({"email": "a@b.c"}, redact=["email"])
    (invocation,) = rec.trace.invocations
    assert invocation.request["kwargs"]["data"] == {"email": "[REDACTED]"}


def test_disable_redaction_with_explicit_empty_engine() -> None:
    rec = _tool_fixture({"api_key": "sk-123"}, redact=RedactionEngine(keys=[]))
    (invocation,) = rec.trace.invocations
    assert invocation.request["kwargs"]["data"] == {"api_key": "sk-123"}


def test_json_round_trip_of_redacted_payload() -> None:
    engine = RedactionEngine()
    value = engine.apply({"token": "abc", "items": [{"secret": "x"}]})
    assert json.loads(json.dumps(value)) == {
        "token": "[REDACTED]",
        "items": [{"secret": "[REDACTED]"}],
    }
