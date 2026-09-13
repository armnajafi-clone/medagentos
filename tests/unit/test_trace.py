"""Trace Engine: ADR-004 says nothing executes untraced. These tests hold it to that."""

from __future__ import annotations

import unittest

from tests.support import ROOT  # noqa: F401

from medagentos.core.entities import TraceEventType
from medagentos.core.errors import StepError
from medagentos.core.trace import TraceEngine
from medagentos.infra.memory import InMemoryUnitOfWork


class TestTraceEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.uow = InMemoryUnitOfWork()
        self.trace = TraceEngine("run_test", self.uow.traces)

    def test_events_are_sequenced_monotonically(self) -> None:
        for i in range(5):
            self.trace.record(TraceEventType.STEP_STARTED, f"step{i}")
        sequences = [e.sequence for e in self.trace.events()]
        self.assertEqual(sequences, [1, 2, 3, 4, 5])

    def test_sequence_not_wall_clock_defines_order(self) -> None:
        # Two events in the same millisecond must still be strictly ordered.
        a = self.trace.record(TraceEventType.STEP_STARTED, "a")
        b = self.trace.record(TraceEventType.STEP_STARTED, "b")
        self.assertLess(a.sequence, b.sequence)

    def test_successful_span_emits_start_and_success(self) -> None:
        with self.trace.step("preprocessing", inputs={"volume": "x"}) as span:
            span.set_output({"voxels": 10})

        events = self.trace.events()
        self.assertEqual(
            [e.type for e in events],
            [TraceEventType.STEP_STARTED, TraceEventType.STEP_SUCCEEDED],
        )
        self.assertIsNotNone(events[1].duration_ms)
        self.assertIsNotNone(events[0].input_digest)
        self.assertIsNotNone(events[1].output_digest)

    def test_failing_span_emits_failure_and_reraises(self) -> None:
        with self.assertRaises(StepError):
            with self.trace.step("segmentation"):
                raise StepError("segmentation", "adapter unavailable")

        events = self.trace.events()
        self.assertIs(events[-1].type, TraceEventType.STEP_FAILED)
        self.assertEqual(events[-1].error_code, "step_failed")
        self.assertEqual(events[-1].error_message, "adapter unavailable")

    def test_failure_stamps_the_run_id_onto_the_error(self) -> None:
        # ERROR_HANDLING.md requires a trace id on every error; a step that
        # raises without knowing its run id must still produce one.
        try:
            with self.trace.step("segmentation"):
                raise StepError("segmentation", "boom")
        except StepError as exc:
            self.assertEqual(exc.trace_id, "run_test")
        else:
            self.fail("the error should have propagated")

    def test_unexpected_exception_is_traced_before_it_escapes(self) -> None:
        with self.assertRaises(ZeroDivisionError):
            with self.trace.step("bad_step"):
                1 / 0

        last = self.trace.events()[-1]
        self.assertIs(last.type, TraceEventType.STEP_FAILED)
        self.assertEqual(last.error_code, "unhandled_exception")
        self.assertIn("ZeroDivisionError", last.error_message or "")

    def test_tool_spans_use_tool_event_types(self) -> None:
        with self.trace.tool("brain_mri.segmentation@0.1.0"):
            pass
        self.assertEqual(
            [e.type for e in self.trace.events()],
            [TraceEventType.TOOL_CALLED, TraceEventType.TOOL_SUCCEEDED],
        )

    def test_traces_of_different_runs_do_not_mix(self) -> None:
        other = TraceEngine("run_other", self.uow.traces)
        self.trace.record(TraceEventType.STEP_STARTED, "mine")
        other.record(TraceEventType.STEP_STARTED, "theirs")

        self.assertEqual([e.name for e in self.trace.events()], ["mine"])
        self.assertEqual([e.name for e in other.events()], ["theirs"])


if __name__ == "__main__":
    unittest.main()
