"""Trace Engine.

ADR-004 makes traceability a first-class feature, and TOOL_CALLING.md requires
that *every* execution creates trace events. This module is the only way events
are produced, so "was it traced?" has one answer rather than one per call site.

The engine records digests and references, never payloads (see the trace-volume
risk in ARCHITECTURE.md).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .entities import JsonDict, TraceEvent, TraceEventType
from .errors import MedAgentError
from .ids import digest_json
from .ports import TraceRepository


class TraceEngine:
    """Append-only event recorder scoped to a single run.

    Sequence numbers are assigned by the engine rather than read from the clock,
    so events stay correctly ordered even when two of them land in the same
    millisecond or the host clock steps backwards.
    """

    def __init__(self, run_id: str, repository: TraceRepository) -> None:
        self._run_id = run_id
        self._repository = repository
        self._sequence = 0

    @property
    def run_id(self) -> str:
        return self._run_id

    def record(
        self,
        event_type: TraceEventType,
        name: str,
        *,
        duration_ms: float | None = None,
        input_digest: str | None = None,
        output_digest: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        **attributes: Any,
    ) -> TraceEvent:
        self._sequence += 1
        event = TraceEvent(
            run_id=self._run_id,
            type=event_type,
            name=name,
            sequence=self._sequence,
            duration_ms=duration_ms,
            input_digest=input_digest,
            output_digest=output_digest,
            error_code=error_code,
            error_message=error_message,
            attributes=dict(attributes),
        )
        return self._repository.append_event(event)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        started: TraceEventType,
        succeeded: TraceEventType,
        failed: TraceEventType,
        inputs: JsonDict | None = None,
        **attributes: Any,
    ) -> Iterator[_Span]:
        """Trace one unit of work, emitting a start event and exactly one outcome event.

        The failure path re-raises: tracing observes execution, it never changes
        it. Errors that carry no trace id yet acquire this run's id, which is
        what lets the API return a trace id for any failure.
        """
        input_digest = digest_json(inputs) if inputs is not None else None
        self.record(started, name, input_digest=input_digest, **attributes)
        span = _Span()
        began = time.perf_counter()
        try:
            yield span
        except MedAgentError as exc:
            if exc.trace_id is None:
                exc.with_trace(self._run_id)
            self.record(
                failed,
                name,
                duration_ms=(time.perf_counter() - began) * 1000,
                input_digest=input_digest,
                error_code=exc.code,
                error_message=exc.message,
                **attributes,
            )
            raise
        except Exception as exc:  # unexpected: still traced, then re-raised
            self.record(
                failed,
                name,
                duration_ms=(time.perf_counter() - began) * 1000,
                input_digest=input_digest,
                error_code="unhandled_exception",
                error_message=f"{type(exc).__name__}: {exc}",
                **attributes,
            )
            raise
        else:
            self.record(
                succeeded,
                name,
                duration_ms=(time.perf_counter() - began) * 1000,
                input_digest=input_digest,
                output_digest=digest_json(span.output) if span.output is not None else None,
                **attributes,
            )

    def step(self, name: str, inputs: JsonDict | None = None, **attributes: Any):
        return self.span(
            name,
            started=TraceEventType.STEP_STARTED,
            succeeded=TraceEventType.STEP_SUCCEEDED,
            failed=TraceEventType.STEP_FAILED,
            inputs=inputs,
            **attributes,
        )

    def tool(self, name: str, inputs: JsonDict | None = None, **attributes: Any):
        return self.span(
            name,
            started=TraceEventType.TOOL_CALLED,
            succeeded=TraceEventType.TOOL_SUCCEEDED,
            failed=TraceEventType.TOOL_FAILED,
            inputs=inputs,
            **attributes,
        )

    def events(self) -> list[TraceEvent]:
        return self._repository.list_events(self._run_id)


class _Span:
    """Handle a traced block uses to hand its result back to the tracer."""

    __slots__ = ("output",)

    def __init__(self) -> None:
        self.output: Any | None = None

    def set_output(self, value: Any) -> None:
        self.output = value
