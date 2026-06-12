"""TF-042: stable boundary fingerprints."""

import pytest
from tracefork.canonicalization import Canonicalizer


def test_fingerprint_is_deterministic_hex64() -> None:
    canonicalizer = Canonicalizer()
    first = canonicalizer.fingerprint("tool.search", "search_orders", {"customer_id": 912})
    second = canonicalizer.fingerprint("tool.search", "search_orders", {"customer_id": 912})
    assert first == second
    assert len(first) == 64
    int(first, 16)  # must be hexadecimal


def test_fingerprint_independent_of_key_order() -> None:
    canonicalizer = Canonicalizer()
    first = canonicalizer.fingerprint("tool", "t", {"a": 1, "b": {"x": 2, "y": 3}})
    second = canonicalizer.fingerprint("tool", "t", {"b": {"y": 3, "x": 2}, "a": 1})
    assert first == second


def test_fingerprint_differs_by_request() -> None:
    canonicalizer = Canonicalizer()
    first = canonicalizer.fingerprint("tool", "t", {"customer_id": 912})
    second = canonicalizer.fingerprint("tool", "t", {"customer_id": 913})
    assert first != second


def test_fingerprint_differs_by_name_and_type() -> None:
    canonicalizer = Canonicalizer()
    base = canonicalizer.fingerprint("tool", "t", {"a": 1})
    assert base != canonicalizer.fingerprint("tool", "other", {"a": 1})
    assert base != canonicalizer.fingerprint("llm", "t", {"a": 1})


def test_fingerprint_excludes_ignored_fields() -> None:
    canonicalizer = Canonicalizer(ignore=["timestamp"])
    first = canonicalizer.fingerprint("tool", "t", {"customer_id": 912, "timestamp": "a"})
    second = canonicalizer.fingerprint("tool", "t", {"customer_id": 912, "timestamp": "b"})
    assert first == second


def test_fingerprint_does_not_include_span_ids_or_occurrence() -> None:
    canonicalizer = Canonicalizer()
    first = canonicalizer.fingerprint("tool", "t", {"a": 1})
    # The fingerprint function has no span/occurrence parameters, so it cannot
    # include them; verify stability across separate instances too.
    second = Canonicalizer().fingerprint("tool", "t", {"a": 1})
    assert first == second


def test_fingerprint_rejects_unsupported_request() -> None:
    canonicalizer = Canonicalizer()

    with pytest.raises(TypeError):
        canonicalizer.fingerprint("tool", "t", {"k": object()})
