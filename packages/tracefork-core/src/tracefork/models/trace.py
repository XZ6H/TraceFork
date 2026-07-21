"""Trace domain model (TF-010)."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracefork.models.provenance import Provenance
from tracefork.models.replay import BoundaryInvocation
from tracefork.models.span import Span

SCHEMA_VERSION = "1.0"


class TraceStatus(StrEnum):
    """Lifecycle status of a trace."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Trace(BaseModel):
    """One complete execution of an agent.

    Every trace carries its ``schema_version`` from the moment of creation
    (ADR 0002). Fixtures are never assumed to survive unversioned changes.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    trace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime | None = None
    input: Any = None
    output: Any | None = None
    spans: list[Span] = Field(default_factory=list)
    invocations: list[BoundaryInvocation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    status: TraceStatus = TraceStatus.RUNNING

    @field_validator("started_at", "completed_at", mode="after")
    @classmethod
    def _normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_ordering(self) -> "Trace":
        if self.completed_at is not None and self.completed_at < self.started_at:
            msg = "completed_at must not be earlier than started_at"
            raise ValueError(msg)
        return self
