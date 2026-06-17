"""Replay session and result reporting (TF-050..054).

A session replays a recorded fixture while the *current* application code
runs. Every boundary call is routed by the replay policy: REPLAY boundaries
are served from the recording without executing anything live (TF-051), LIVE
boundaries execute for real and are counted. Matching is exact (fingerprint,
parent context, occurrence); an unmatched call fails closed with diagnostics
and never falls back to a live call (ADR 0003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import TracebackType
from typing import Any, Literal

from tracefork.boundaries.base import boundary_span_kind
from tracefork.boundaries.registry import BoundaryRegistry
from tracefork.canonicalization import Canonicalizer
from tracefork.errors import AdapterError, ReplayError, ReplayMismatchError
from tracefork.models import (
    BoundaryInvocation,
    ExecutionMode,
    ReplayPolicy,
    Trace,
    TraceStatus,
)
from tracefork.recording.context import (
    ExecutionContext,
    current_execution,
    current_recording,
    current_span,
)
from tracefork.recording.recorder import Recording
from tracefork.replay.matcher import MatchMiss, ReplayMatcher, format_mismatch
from tracefork.serialization import FixtureEnvelope

_NETWORK_FAMILIES = {"llm", "http"}


class ReplayStatus(StrEnum):
    """Outcome status of a replay run."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass
class ReplayResult:
    """Report of one replay execution (TF-052)."""

    status: ReplayStatus
    trace: Trace
    matched: int
    unexpected: list[MatchMiss] = field(default_factory=list)
    unused_recordings: list[BoundaryInvocation] = field(default_factory=list)
    live_boundaries: list[str] = field(default_factory=list)
    execution_error: BaseException | None = None

    @property
    def network_calls(self) -> int:
        """Live calls that touch an external dependency (LLM/HTTP families)."""
        return sum(
            1 for boundary in self.live_boundaries if boundary.split(".", 1)[0] in _NETWORK_FAMILIES
        )

    @property
    def is_hermetic(self) -> bool:
        """True when nothing live ran and nothing went unmatched (TF-053)."""
        return not self.live_boundaries and not self.unexpected


class ReplaySession:
    """Replays a fixture under a policy while current application code runs.

    Usage::

        session = ReplaySession(fixture=fixture, registry=registry)
        with session:
            await agent.run(...)      # boundaries are intercepted
        report = session.result
    """

    def __init__(
        self,
        fixture: FixtureEnvelope,
        policy: ReplayPolicy | None = None,
        registry: BoundaryRegistry | None = None,
        canonicalizer: Canonicalizer | None = None,
    ) -> None:
        self.fixture = fixture
        self.policy = policy if policy is not None else ReplayPolicy()
        self.registry = registry if registry is not None else BoundaryRegistry()
        self.canonicalizer = canonicalizer if canonicalizer is not None else Canonicalizer()
        self._matcher = ReplayMatcher(fixture.trace.invocations)
        self._recording: Recording | None = None
        self._recording_token: Any = None
        self._execution_token: Any = None
        self.matched: list[BoundaryInvocation] = []
        self.unexpected: list[MatchMiss] = []
        self.live_boundaries: list[str] = []
        self._execution_error: BaseException | None = None
        self._result: ReplayResult | None = None

    def __enter__(self) -> ReplaySession:
        self._recording = Recording(self.fixture.trace.name, input=self.fixture.trace.input)
        self._recording_token = current_recording.set(self._recording)
        self._execution_token = current_execution.set(
            ExecutionContext(
                mode=ExecutionMode.REPLAY,
                recording=self._recording,
                replay_session=self,
                policy=self.policy,
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if self._execution_token is not None:
            current_execution.reset(self._execution_token)
            self._execution_token = None
        if self._recording_token is not None:
            current_recording.reset(self._recording_token)
            self._recording_token = None
        self._execution_error = exc_value
        self._build_result()
        return False

    @property
    def result(self) -> ReplayResult:
        if self._result is None:
            msg = "replay session has not run; use it as a context manager first"
            raise ReplayError(msg)
        return self._result

    async def replay_invoke(
        self,
        boundary_type: str,
        name: str,
        request: Any,
        metadata: dict[str, Any],
    ) -> Any:
        """Serve one boundary call from the recording, or fail closed (TF-051).

        ``call_live`` is deliberately absent: a replayed boundary can never
        execute the real call, by construction.
        """
        try:
            canonical_request = self.canonicalizer.canonical_request(request)
            fingerprint = self.canonicalizer.fingerprint(boundary_type, name, request)
        except TypeError as exc:
            msg = f"boundary {boundary_type}.{name} request is not canonicalizable: {exc}"
            raise AdapterError(msg) from exc

        parent = current_span.get()
        parent_span_id = parent.span_id if parent is not None else None
        parent_name = parent.name if parent is not None else None

        outcome = self._matcher.match(
            boundary_type,
            name,
            canonical_request,
            fingerprint=fingerprint,
            parent_name=parent_name,
        )
        if isinstance(outcome, MatchMiss):
            self.unexpected.append(outcome)
            raise ReplayMismatchError(format_mismatch(outcome))

        recorded = outcome.invocation
        handler = self.registry.handler_for(boundary_type)
        native_response = handler.restore(recorded.response, dict(recorded.metadata))

        recording = self._recording
        assert recording is not None
        span = recording.start_span(
            name, boundary_span_kind(boundary_type), input=canonical_request
        )
        span.output = recorded.response
        span.attributes["replay"] = "replayed"
        recording.finish_span(span, None)
        recording.trace.invocations.append(
            recorded.model_copy(
                update={
                    "span_id": span.span_id,
                    "parent_span_id": parent_span_id,
                    "parent_name": parent_name,
                    "metadata": {**recorded.metadata, "replay": "replayed"},
                }
            )
        )
        self.matched.append(recorded)
        return native_response

    def note_live(self, boundary_type: str, name: str) -> None:
        """Record that a boundary executed live (selective replay, TF-091)."""
        self.live_boundaries.append(f"{boundary_type}.{name}")

    def _build_result(self) -> None:
        recording = self._recording
        assert recording is not None
        failed = self._execution_error is not None or bool(self.unexpected)
        trace = recording.trace
        trace.status = TraceStatus.FAILED if failed else TraceStatus.COMPLETED
        trace.completed_at = datetime.now(UTC)
        self._result = ReplayResult(
            status=ReplayStatus.FAILED if failed else ReplayStatus.PASSED,
            trace=trace,
            matched=len(self.matched),
            unexpected=list(self.unexpected),
            unused_recordings=self._matcher.unused(),
            live_boundaries=list(self.live_boundaries),
            execution_error=self._execution_error,
        )
