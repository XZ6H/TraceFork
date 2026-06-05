"""Span domain model (TF-010)."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SpanKind(StrEnum):
    """Kind of execution unit."""

    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    HTTP = "http"
    RETRIEVER = "retriever"
    WORKFLOW = "workflow"
    CUSTOM = "custom"


class SpanStatus(StrEnum):
    """Lifecycle status of a span."""

    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


class SpanError(BaseModel):
    """Exception information for a failed span (TF-024).

    Stack traces are deliberately not persisted: they routinely contain local
    filesystem paths and other sensitive context.
    """

    model_config = ConfigDict(extra="forbid")

    exception_type: str = Field(min_length=1)
    message: str = ""


class Span(BaseModel):
    """One execution unit within a trace.

    ``span_id`` and ``parent_span_id`` form the execution tree from which the
    logical graph is derived (ADR 0005). Sibling order is start order, never
    completion order, so parallel spans remain deterministic.
    """

    model_config = ConfigDict(extra="forbid")

    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    kind: SpanKind
    name: str = Field(min_length=1)
    input: Any = None
    output: Any | None = None
    started_at: datetime
    completed_at: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    status: SpanStatus = SpanStatus.RUNNING
    error: SpanError | None = None

    @field_validator("started_at", "completed_at", mode="after")
    @classmethod
    def _normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
