"""Canonicalizer: canonical requests and stable fingerprints (TF-040, TF-042)."""

import hashlib
from typing import Any

from tracefork.canonicalization.json import canonical_json_bytes, canonicalize
from tracefork.canonicalization.rules import IgnoreRules


class Canonicalizer:
    """Applies ignore rules and computes deterministic fingerprints.

    The fingerprint covers the boundary type, the boundary name and the
    canonical request — never timestamps, span IDs or occurrence indices.
    """

    def __init__(self, ignore: list[str] | None = None) -> None:
        self._rules = IgnoreRules(ignore or [])

    def canonical_request(self, value: Any) -> Any:
        """Return the JSON-ready canonical form of a request payload."""
        return self._rules.apply(canonicalize(value))

    def fingerprint(self, boundary_type: str, name: str, request: Any) -> str:
        """Compute the SHA-256 fingerprint of a boundary call."""
        payload = {
            "boundary_type": boundary_type,
            "name": name,
            "request": self.canonical_request(request),
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
