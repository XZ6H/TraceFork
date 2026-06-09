"""TF-011 / TF-013: fixture envelope, canonical serialization, integrity."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from tracefork.canonicalization import canonical_json, canonical_json_bytes
from tracefork.errors import FixtureCorruptError, FixtureVersionError
from tracefork.models import Provenance, Trace
from tracefork.serialization import (
    SUPPORTED_FIXTURE_VERSION,
    build_envelope,
    parse_envelope,
)

T0 = datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC)


def make_trace() -> Trace:
    return Trace(trace_id="tr_1", name="case", started_at=T0, provenance=Provenance())


class TestCanonicalJson:
    def test_sorted_keys_and_compact_separators(self) -> None:
        assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_stable_across_insertion_order_and_nesting(self) -> None:
        a = canonical_json({"a": 1, "b": {"y": 2, "x": 3}})
        b = canonical_json({"b": {"x": 3, "y": 2}, "a": 1})
        assert a == b == '{"a":1,"b":{"x":3,"y":2}}'

    def test_datetime_normalized_to_utc_z_suffix(self) -> None:
        plus_two = timezone(timedelta(hours=2))
        offset_instant = datetime(2026, 9, 2, 17, 0, 0, tzinfo=plus_two)
        assert canonical_json(offset_instant) == '"2026-09-02T15:00:00Z"'
        assert canonical_json(T0) == '"2026-09-02T15:00:00Z"'

    def test_naive_datetime_treated_as_utc(self) -> None:
        assert canonical_json(datetime(2026, 9, 2, 15, 0, 0)) == '"2026-09-02T15:00:00Z"'

    def test_output_is_utf8(self) -> None:
        assert canonical_json_bytes({"k": "café"}).decode("utf-8") == '{"k":"café"}'

    def test_unsupported_type_rejected(self) -> None:
        with pytest.raises(TypeError):
            canonical_json({"k": object()})

    def test_non_finite_float_rejected(self) -> None:
        with pytest.raises(TypeError):
            canonical_json(float("nan"))


class TestEnvelope:
    def test_build_sets_version_and_digest(self) -> None:
        envelope = build_envelope(make_trace(), created_at=T0)
        assert envelope.fixture_version == SUPPORTED_FIXTURE_VERSION == "1"
        assert envelope.integrity.algorithm == "sha256"
        assert len(envelope.integrity.digest) == 64

    def test_digest_is_stable_for_identical_payload(self) -> None:
        first = build_envelope(make_trace(), created_at=T0)
        second = build_envelope(make_trace(), created_at=T0)
        assert first.integrity.digest == second.integrity.digest

    def test_digest_covers_trace_payload(self) -> None:
        changed = make_trace()
        changed.output = {"answer": 42}
        assert (
            build_envelope(make_trace(), created_at=T0).integrity.digest
            != build_envelope(changed, created_at=T0).integrity.digest
        )

    def test_json_round_trip(self) -> None:
        envelope = build_envelope(make_trace(), created_at=T0)
        restored = parse_envelope(envelope.to_json_bytes())
        assert restored == envelope

    def test_corrupted_trace_detected(self) -> None:
        envelope = build_envelope(make_trace(), created_at=T0)
        data = json.loads(envelope.to_json_bytes())
        data["trace"]["name"] = "tampered"
        with pytest.raises(FixtureCorruptError):
            parse_envelope(json.dumps(data).encode("utf-8"))

    def test_corrupted_digest_detected(self) -> None:
        envelope = build_envelope(make_trace(), created_at=T0)
        data = json.loads(envelope.to_json_bytes())
        data["integrity"]["digest"] = "0" * 64
        with pytest.raises(FixtureCorruptError):
            parse_envelope(json.dumps(data).encode("utf-8"))

    def test_unknown_fixture_version_rejected(self) -> None:
        envelope = build_envelope(make_trace(), created_at=T0)
        data = json.loads(envelope.to_json_bytes())
        data["fixture_version"] = "999"
        with pytest.raises(FixtureVersionError):
            parse_envelope(json.dumps(data).encode("utf-8"))

    def test_missing_version_rejected(self) -> None:
        envelope = build_envelope(make_trace(), created_at=T0)
        data = json.loads(envelope.to_json_bytes())
        del data["fixture_version"]
        with pytest.raises(FixtureVersionError):
            parse_envelope(json.dumps(data).encode("utf-8"))

    def test_missing_integrity_rejected(self) -> None:
        envelope = build_envelope(make_trace(), created_at=T0)
        data = json.loads(envelope.to_json_bytes())
        del data["integrity"]
        with pytest.raises(FixtureCorruptError):
            parse_envelope(json.dumps(data).encode("utf-8"))

    def test_invalid_json_rejected(self) -> None:
        with pytest.raises(FixtureCorruptError):
            parse_envelope(b"not json at all")

    def test_non_object_json_rejected(self) -> None:
        with pytest.raises(FixtureCorruptError):
            parse_envelope(b"[1, 2, 3]")
