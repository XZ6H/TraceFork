"""Replay and boundary domain models (TF-030, TF-032)."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionMode(StrEnum):
    """Runtime mode of the boundary layer for one execution."""

    NORMAL = "normal"
    RECORD = "record"
    REPLAY = "replay"


class ReplayMode(StrEnum):
    """Explicit decision for a single boundary inside a replay (Rule 3).

    Never chosen implicitly: the resolution order is exact boundary rule,
    boundary-type rule, then default.
    """

    LIVE = "live"
    REPLAY = "replay"
    MOCK = "mock"
    FAULT = "fault"


class ReplayPolicy(BaseModel):
    """Declares which boundaries run live and which are served from recordings."""

    model_config = ConfigDict(extra="forbid")

    default: ReplayMode = ReplayMode.REPLAY
    llm: ReplayMode | None = None
    http: ReplayMode | None = None
    families: dict[str, ReplayMode] = Field(default_factory=dict)
    tools: dict[str, ReplayMode] = Field(default_factory=dict)

    def mode_for(self, boundary_type: str, name: str) -> ReplayMode:
        """Resolve the replay mode for a boundary call.

        Order: exact boundary rule (tool name), then boundary-family rule,
        then default.
        """
        family = boundary_type.split(".", 1)[0]
        if family == "tool" and name in self.tools:
            return self.tools[name]
        family_rule = self.families.get(family)
        if family_rule is not None:
            return family_rule
        type_rule = {"llm": self.llm, "http": self.http}.get(family)
        if type_rule is not None:
            return type_rule
        return self.default


class BoundaryRequest(BaseModel):
    """Canonical request data for one boundary call."""

    model_config = ConfigDict(extra="forbid")

    boundary_type: str = Field(min_length=1)
    name: str = Field(min_length=1)
    request: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BoundaryResponse(BaseModel):
    """Canonical response data for one boundary call."""

    model_config = ConfigDict(extra="forbid")

    response: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BoundaryInvocation(BaseModel):
    """One recorded boundary interaction; the unit of replay matching (TF-033).

    ``fingerprint`` covers the boundary type, name and canonical request.
    ``occurrence`` disambiguates repeated identical calls. ``parent_name``
    gives matching a stable logical parent anchor (span IDs are not stable
    across executions).
    """

    model_config = ConfigDict(extra="forbid")

    boundary_type: str = Field(min_length=1)
    name: str = Field(min_length=1)
    request: Any = None
    response: Any | None = None
    fingerprint: str | None = None
    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    parent_name: str | None = None
    occurrence: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
