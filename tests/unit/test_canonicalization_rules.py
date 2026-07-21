"""TF-040 / TF-041: canonicalization ignore rules via small JSON-path syntax.

Syntax deliberately small for v0.1:
- dotted paths (``headers.authorization``) ignore that exact location,
- bare names (``timestamp``) ignore that key at any nesting level.
"""

import pytest

from tracefork.canonicalization import Canonicalizer, IgnoreRules, canonical_json


def test_ignore_removes_top_level_key() -> None:
    rules = IgnoreRules(["timestamp"])
    assert rules.apply({"customer_id": 42, "timestamp": "2026-09-02T15:00:00Z"}) == {
        "customer_id": 42
    }


def test_ignore_dotted_path_removes_exact_location() -> None:
    rules = IgnoreRules(["headers.authorization"])
    value = {"customer_id": 42, "headers": {"authorization": "Bearer x", "accept": "json"}}
    assert rules.apply(value) == {"customer_id": 42, "headers": {"accept": "json"}}


def test_ignore_bare_name_removes_key_at_any_level() -> None:
    rules = IgnoreRules(["request_id"])
    value = {"request_id": "r1", "meta": {"request_id": "r2", "keep": True}}
    assert rules.apply(value) == {"meta": {"keep": True}}


def test_dotted_path_matches_only_its_location() -> None:
    rules = IgnoreRules(["a.b"])
    value = {"x": {"a": {"b": 1}}, "a": {"b": 2}}
    assert rules.apply(value) == {"x": {"a": {"b": 1}}, "a": {}}


def test_rules_reach_dictionaries_inside_lists() -> None:
    rules = IgnoreRules(["trace_id"])
    value = {"items": [{"trace_id": "t1", "v": 1}, {"trace_id": "t2", "v": 2}]}
    assert rules.apply(value) == {"items": [{"v": 1}, {"v": 2}]}


def test_no_rules_leaves_value_unchanged() -> None:
    rules = IgnoreRules([])
    value = {"a": {"b": [1, 2]}}
    assert rules.apply(value) == value


def test_empty_rule_rejected() -> None:
    with __import__("pytest").raises(ValueError):
        IgnoreRules([""])


def test_canonicalizer_applies_rules_before_canonical_form() -> None:
    canonicalizer = Canonicalizer(ignore=["timestamp"])
    result = canonicalizer.canonical_request({"b": True, "a": 1, "timestamp": 7})
    assert result == {"a": 1, "b": True}


def test_canonicalizer_rejects_unsupported_objects() -> None:
    canonicalizer = Canonicalizer()

    class Mystery:
        pass

    try:
        canonicalizer.canonical_request({"k": Mystery()})
    except TypeError as exc:
        assert "Mystery" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected TypeError")


def test_int_and_str_key_collision_rejected() -> None:
    # {1: "a"} and {"1": "b"} would both map to key "1", silently losing data
    # and corrupting fingerprints — reject instead.
    with pytest.raises(TypeError, match="conflict"):
        canonical_json({1: "a", "1": "b"})


def test_bool_key_collision_rejected() -> None:
    with pytest.raises(TypeError, match="conflict"):
        canonical_json({True: "a", "True": "b"})


def test_non_string_keys_without_collision_are_converted() -> None:
    assert canonical_json({1: "a", 2: "b"}) == '{"1":"a","2":"b"}'


def test_very_deep_nesting_raises_recursion_error_not_silent_loss() -> None:
    deep: dict = {}
    current = deep
    for _ in range(20000):
        current["child"] = {}
        current = current["child"]
    with pytest.raises(RecursionError):
        canonical_json(deep)
