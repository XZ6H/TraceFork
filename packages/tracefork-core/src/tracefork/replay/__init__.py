"""Replay engine: matching, sessions and fail-closed execution."""

from tracefork.replay.matcher import (
    MatchMiss,
    MatchSuccess,
    ReplayMatcher,
    describe_difference,
    format_mismatch,
    similarity,
)

__all__ = [
    "MatchMiss",
    "MatchSuccess",
    "ReplayMatcher",
    "describe_difference",
    "format_mismatch",
    "similarity",
]
