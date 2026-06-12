"""TF-044 / TF-045: replay matcher and mismatch diagnostics.

Matching strategy v1, in order: exact fingerprint, parent context, occurrence.
No fuzzy matching during replay — fuzzy similarity appears only in
diagnostics.
"""

from tracefork.canonicalization import Canonicalizer
from tracefork.models import BoundaryInvocation
from tracefork.replay.matcher import (
    MatchMiss,
    MatchSuccess,
    ReplayMatcher,
    describe_difference,
    format_mismatch,
)

CANONICALIZER = Canonicalizer()


def recorded(
    fingerprint: str,
    *,
    parent_name: str | None = None,
    occurrence: int = 0,
    request: dict[str, object] | None = None,
) -> BoundaryInvocation:
    return BoundaryInvocation(
        boundary_type="tool.search",
        name="search_orders",
        request=request if request is not None else {"customer_id": 912},
        response={"orders": [1]},
        fingerprint=fingerprint,
        span_id="sp_recorded",
        parent_span_id="sp_parent_recorded",
        parent_name=parent_name,
        occurrence=occurrence,
    )


def fingerprint_for(request: dict[str, object]) -> str:
    return CANONICALIZER.fingerprint("tool.search", "search_orders", request)


class TestMatching:
    def test_exact_fingerprint_matches(self) -> None:
        fp = fingerprint_for({"customer_id": 912})
        matcher = ReplayMatcher([recorded(fp)])
        result = matcher.match(
            "tool.search", "search_orders", {"customer_id": 912}, fingerprint=fp, parent_name=None
        )
        assert isinstance(result, MatchSuccess)
        assert result.invocation.response == {"orders": [1]}
        assert matcher.unused() == []

    def test_repeated_calls_consume_occurrences_in_order(self) -> None:
        fp = fingerprint_for({"customer_id": 912})
        matcher = ReplayMatcher(
            [recorded(fp, occurrence=0), recorded(fp, occurrence=1), recorded(fp, occurrence=2)]
        )
        for _ in range(3):
            result = matcher.match(
                "tool.search",
                "search_orders",
                {"customer_id": 912},
                fingerprint=fp,
                parent_name=None,
            )
            assert isinstance(result, MatchSuccess)
        fourth = matcher.match(
            "tool.search", "search_orders", {"customer_id": 912}, fingerprint=fp, parent_name=None
        )
        assert isinstance(fourth, MatchMiss)

    def test_parent_context_is_preferred(self) -> None:
        fp = fingerprint_for({"customer_id": 912})
        matcher = ReplayMatcher(
            [
                recorded(fp, parent_name="agent-a", occurrence=0),
                recorded(fp, parent_name="agent-b", occurrence=0),
            ]
        )
        result = matcher.match(
            "tool.search",
            "search_orders",
            {"customer_id": 912},
            fingerprint=fp,
            parent_name="agent-b",
        )
        assert isinstance(result, MatchSuccess)
        assert result.invocation.parent_name == "agent-b"

    def test_fingerprint_mismatch_is_a_miss(self) -> None:
        fp = fingerprint_for({"customer_id": 912})
        matcher = ReplayMatcher([recorded(fp)])
        miss = matcher.match(
            "tool.search",
            "search_orders",
            {"customer_id": 912, "region": "EU"},
            fingerprint=fingerprint_for({"customer_id": 912, "region": "EU"}),
            parent_name=None,
        )
        assert isinstance(miss, MatchMiss)
        assert miss.closest is not None
        assert 0.0 < miss.similarity < 1.0

    def test_miss_even_when_only_parent_differs_and_all_consumed(self) -> None:
        fp = fingerprint_for({"customer_id": 912})
        matcher = ReplayMatcher([recorded(fp, parent_name="agent-a")])
        matcher.match(
            "tool.search",
            "search_orders",
            {"customer_id": 912},
            fingerprint=fp,
            parent_name="agent-a",
        )
        second = matcher.match(
            "tool.search",
            "search_orders",
            {"customer_id": 912},
            fingerprint=fp,
            parent_name="agent-a",
        )
        assert isinstance(second, MatchMiss)

    def test_unused_lists_unconsumed_recordings(self) -> None:
        fp_a = fingerprint_for({"customer_id": 912})
        fp_b = fingerprint_for({"customer_id": 913})
        matcher = ReplayMatcher([recorded(fp_a), recorded(fp_b)])
        matcher.match(
            "tool.search", "search_orders", {"customer_id": 912}, fingerprint=fp_a, parent_name=None
        )
        unused = matcher.unused()
        assert len(unused) == 1
        assert unused[0].fingerprint == fp_b


class TestDiagnostics:
    def test_describe_difference_reports_added_key(self) -> None:
        lines = describe_difference({"customer_id": 912, "region": "EU"}, {"customer_id": 912})
        assert lines == ["+ region = 'EU'"]

    def test_describe_difference_reports_changed_and_removed(self) -> None:
        lines = describe_difference({"a": 2}, {"a": 1, "b": 3})
        assert sorted(lines) == ["- b", "~ a: 1 -> 2"]

    def test_format_mismatch_contains_full_diagnostics(self) -> None:
        fp = fingerprint_for({"customer_id": 912})
        matcher = ReplayMatcher([recorded(fp)])
        miss = matcher.match(
            "tool.search",
            "search_orders",
            {"customer_id": 912, "region": "EU"},
            fingerprint=fingerprint_for({"customer_id": 912, "region": "EU"}),
            parent_name=None,
        )
        assert isinstance(miss, MatchMiss)
        text = format_mismatch(miss)
        assert "tool.search" in text
        assert "search_orders" in text
        assert "region" in text
        assert "%" in text
        assert "no silent fallback" in text.lower()
