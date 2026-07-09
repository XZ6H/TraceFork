"""Replay matching and mismatch diagnostics (TF-044, TF-045).

Matching strategy v1, applied in order:

1. exact fingerprint (boundary type + name + canonical request),
2. parent context (recorded ``parent_name`` equal to the incoming one),
3. occurrence order (lowest unconsumed first).

No fuzzy matching happens during replay. Fuzzy similarity appears only in
diagnostics, to point at the closest recorded interaction.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from tracefork.canonicalization import canonical_json
from tracefork.models import BoundaryInvocation


@dataclass
class MatchSuccess:
    """An incoming call was matched to exactly one recorded invocation."""

    invocation: BoundaryInvocation


@dataclass
class MatchMiss:
    """No recorded interaction matched; carries diagnostics (TF-045)."""

    boundary_type: str
    name: str
    request: Any
    fingerprint: str
    parent_name: str | None
    candidates: list[BoundaryInvocation] = field(default_factory=list)
    closest: BoundaryInvocation | None = None
    similarity: float = 0.0


@dataclass
class _Consumable:
    index: int
    invocation: BoundaryInvocation


class ReplayMatcher:
    """Matches incoming boundary calls against recorded invocations.

    Each recorded invocation is consumed at most once; ``unused()`` reports
    recordings the replay never needed (M5 surfaces them as warnings).
    """

    def __init__(self, recorded: list[BoundaryInvocation]) -> None:
        self._recorded = list(recorded)
        self._consumed: set[int] = set()

    def match(
        self,
        boundary_type: str,
        name: str,
        request: Any,
        *,
        fingerprint: str,
        parent_name: str | None,
    ) -> MatchSuccess | MatchMiss:
        available = [
            _Consumable(index, invocation)
            for index, invocation in enumerate(self._recorded)
            if index not in self._consumed
        ]
        candidates = [c for c in available if c.invocation.fingerprint == fingerprint]
        if not candidates:
            return self._miss(boundary_type, name, request, fingerprint, parent_name, available)

        # Criterion 2: prefer candidates recorded under the same logical parent.
        parent_matched = [c for c in candidates if c.invocation.parent_name == parent_name]
        pool = parent_matched if parent_matched else candidates

        # Criterion 3: lowest occurrence first (deterministic).
        pool.sort(key=lambda c: (c.invocation.occurrence, c.index))
        chosen = pool[0]
        self._consumed.add(chosen.index)
        return MatchSuccess(invocation=chosen.invocation)

    def unused(self) -> list[BoundaryInvocation]:
        """Recordings that were never matched."""
        return [
            invocation
            for index, invocation in enumerate(self._recorded)
            if index not in self._consumed
        ]

    def _miss(
        self,
        boundary_type: str,
        name: str,
        request: Any,
        fingerprint: str,
        parent_name: str | None,
        available: list[_Consumable],
    ) -> MatchMiss:
        closest, similarity = _closest_match(request, [c.invocation for c in available])
        return MatchMiss(
            boundary_type=boundary_type,
            name=name,
            request=request,
            fingerprint=fingerprint,
            parent_name=parent_name,
            candidates=[c.invocation for c in available],
            closest=closest,
            similarity=similarity,
        )


def similarity(received: Any, recorded: Any) -> float:
    """Fuzzy similarity between two payloads, for diagnostics only."""
    left = canonical_json(received)
    right = canonical_json(recorded)
    return difflib.SequenceMatcher(None, left, right).ratio()


def describe_difference(received: Any, recorded: Any) -> list[str]:
    """Human-readable difference lines between received and recorded payloads."""
    lines: list[str] = []
    _diff(received, recorded, "", lines)
    return lines


def format_mismatch(miss: MatchMiss) -> str:
    """Build the full diagnostic message for a replay mismatch (TF-045)."""
    lines = [
        f"Replay mismatch: no recorded interaction matches {miss.boundary_type}.{miss.name}",
        "",
        "Received:",
        canonical_json(miss.request),
        "",
    ]
    if miss.closest is not None:
        lines.extend(
            [
                f"Closest recorded interaction (similarity {miss.similarity:.0%}):",
                canonical_json(miss.closest.request),
                "",
            ]
        )
        differences = describe_difference(miss.request, miss.closest.request)
        if differences:
            lines.append("Difference:")
            lines.extend(f"  {line}" for line in differences)
        elif miss.closest.name != miss.name or miss.closest.boundary_type != miss.boundary_type:
            lines.append(
                "The arguments match a recorded interaction, but its boundary name differs: "
                f"received {miss.boundary_type}.{miss.name}, "
                f"recorded {miss.closest.boundary_type}.{miss.closest.name}."
            )
        else:
            lines.append("The payloads differ only in fields excluded from matching.")
    else:
        lines.append("No unconsumed recorded interactions remain for this boundary.")
    lines.extend(
        [
            "",
            "Replay fails closed: no live call was made and no silent fallback occurred.",
        ]
    )
    return "\n".join(lines)


def _closest_match(
    request: Any, recorded: list[BoundaryInvocation]
) -> tuple[BoundaryInvocation | None, float]:
    best: tuple[BoundaryInvocation | None, float] = (None, 0.0)
    for invocation in recorded:
        score = similarity(request, invocation.request)
        if score > best[1]:
            best = (invocation, score)
    return best


def _diff(received: Any, recorded: Any, path: str, lines: list[str]) -> None:
    if isinstance(received, dict) and isinstance(recorded, dict):
        for key in sorted(set(received) | set(recorded)):
            sub_path = f"{path}.{key}" if path else str(key)
            if key not in recorded:
                lines.append(f"+ {sub_path} = {received[key]!r}")
            elif key not in received:
                lines.append(f"- {sub_path}")
            else:
                _diff(received[key], recorded[key], sub_path, lines)
    elif received != recorded:
        lines.append(f"~ {path}: {recorded!r} -> {received!r}")
