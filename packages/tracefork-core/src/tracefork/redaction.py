"""Redaction before persistence (TF-170..173, PRD §29).

Secrets are stripped when data is recorded — never when a fixture is read —
so sensitive values never reach disk. Key-based redaction is on by default
with a conservative secret-key list; custom redactors run afterwards and can
scrub anything else.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

REDACTED = "[REDACTED]"

DEFAULT_SECRET_KEYS = frozenset(
    {"authorization", "api_key", "token", "secret", "password", "cookie"}
)

Redactor = Callable[[Any], Any]


def redactor(func: Redactor) -> Redactor:
    """Mark a function as a custom redactor (documentation + identity)."""
    return func


class RedactionEngine:
    """Deep-walks payloads, replacing secret keys and running custom redactors."""

    def __init__(
        self,
        keys: frozenset[str] | set[str] | None = None,
        redactors: list[Redactor] | None = None,
    ) -> None:
        self.keys = frozenset(keys) if keys is not None else DEFAULT_SECRET_KEYS
        self._redactors = list(redactors) if redactors else []

    def apply(self, value: Any) -> Any:
        """Return a redacted copy of *value*; scalars pass through untouched."""
        redacted = self._apply_keys(value)
        for scrub in self._redactors:
            redacted = scrub(redacted)
        return redacted

    def _apply_keys(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._apply_keys(item) if str(key).lower() not in self.keys else REDACTED
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._apply_keys(item) for item in value]
        return value
