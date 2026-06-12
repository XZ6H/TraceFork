"""Ignore rules for canonicalization (TF-041).

Deliberately small syntax for v0.1:

- dotted paths (``headers.authorization``) ignore that exact location,
- bare names (``timestamp``) ignore that key at any nesting level.

List items are traversed but not addressable by index.
"""

from typing import Any


class IgnoreRules:
    """Removes volatile fields before fingerprinting."""

    def __init__(self, paths: list[str]) -> None:
        rules: list[tuple[str, ...]] = []
        for path in paths:
            parts = tuple(part for part in path.split("."))
            if not path or any(not part for part in parts):
                msg = f"invalid ignore rule {path!r}: must be a non-empty dotted path"
                raise ValueError(msg)
            rules.append(parts)
        self._rules = rules

    def apply(self, value: Any) -> Any:
        """Return a copy of *value* with all ignored fields removed."""
        return self._apply(value, ())

    def _apply(self, value: Any, path: tuple[str, ...]) -> Any:
        if isinstance(value, dict):
            return {
                key: self._apply(item, (*path, str(key)))
                for key, item in value.items()
                if not self._ignored((*path, str(key)))
            }
        if isinstance(value, list):
            return [self._apply(item, path) for item in value]
        return value

    def _ignored(self, path: tuple[str, ...]) -> bool:
        for rule in self._rules:
            if len(rule) == 1:
                # Bare name: ignored at any nesting level.
                if path and path[-1] == rule[0]:
                    return True
            elif path == rule:
                return True
        return False
