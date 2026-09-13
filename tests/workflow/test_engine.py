"""Workflow Runtime: determinism, tracing, error handling, versioning, pausing."""

from __future__ import annotations

import unittest

from tests.support import ROOT, build_engine  # noqa: F401

from medagentos.core.capabilities import Capability, IOField
from medagentos.core.entities import ArtifactType, RunStatus, TraceEventType
from medagentos.core.errors import StepError, ValidationError, WorkflowError, WorkflowNotFoundError
from medagentos.core.workflow import (
    WorkflowContext,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowStep,
)


def _double(context: WorkflowContext) -> dict:
    return {"value": context.require("value") * 2}


def _add_ten(context: WorkflowContext) -> dict:
    return {"value": context.require("value") + 10}


def _linear_workflow(**kwargs) -> WorkflowDefinition:
    defaults = dict(
        name="arithmetic",
        version="1.0.0",
        description="A domain-free workflow used to test the runtime itself.",
        steps=(
            WorkflowStep("double", _double),
            WorkflowStep("add_ten", _add_ten),
        ),
        required_inputs=("value",),
    )
    defaults.update(kwargs)
    return WorkflowDefinition(**defaults)


class TestExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.engine, self.uow, self.workflows, self.capabilities = build_engine()
        self.workflows.register(_linear_workflow())

    def test_steps_run_in_order_and_state_accumulates(self) -> None:
        result = self.engine.start("arithmetic", {"value": 5})

        self.assertIs(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.state["value"], 20)  # (5 * 2) + 10
        self.assertEqual(result.run.completed_steps, ("double", "add_ten"))
        self.assertIsNotNone(result.run.finished_at)

    def test_the_run_is_persisted(self) -> None:
        result = self.engine.start("arithmetic", {"value": 1})
        stored = self.uow.runs.get_run(result.run.id)
        self.assertIsNotNone(stored)
        self.assertIs(stored.status, RunStatus.SUCCEEDED)
        self.assertEqual(stored.outputs["value"], 12)

    def test_the_run_records_the_definition_version_it_executed(self) -> None:
        result = self.engine.start("arithmetic", {"value": 1})
        self.assertEqual(result.run.workflow_version, "1.0.0")

    def test_missing_required_input_is_rejected_before_anything_runs(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            self.engine.start("arithmetic", {})
        self.assertEqual(ctx.exception.details["missing"], ["value"])
        self.assertEqual(self.uow.runs.list_runs(), [], "no run should have been created")

    def test_unknown_workflow_raises(self) -> None:
        with self.assertRaises(WorkflowNotFoundError):
            self.engine.start("does_not_exist", {})


class TestDeterminism(unittest.TestCase):
    """D-09: same definition, same inputs, same seed -> same result and digests."""

    def setUp(self) -> None:
        self.workflows = WorkflowRegistry()

        def sample(context: WorkflowContext) -> dict:
            return {"draw": context.rng.random(), "value": context.require("value")}

        self.workflows.register(
            WorkflowDefinition(
                name="seeded",
                version="1.0.0",
                description="",
                steps=(WorkflowStep("sample", sample),),
                required_inputs=("value",),
            )
        )

    def test_same_seed_reproduces_the_same_draw(self) -> None:
        engine_a, *_ = build_engine(workflows=self.workflows)
        first = engine_a.start("seeded", {"value": 1}, seed=42)
        second = engine_a.start("seeded", {"value": 1}, seed=42)
        self.assertEqual(first.state["draw"], second.state["draw"])

    def test_different_seeds_diverge(self) -> None:
        engine, *_ = build_engine(workflows=self.workflows)
        a = engine.start("seeded", {"value": 1}, seed=1)
        b = engine.start("seeded", {"value": 1}, seed=2)
        self.assertNotEqual(a.state["draw"], b.state["draw"])

    def test_input_digest_is_order_independent(self) -> None:
        engine, *_ = build_engine(workflows=self.workflows)
        a = engine.start("seeded", {"value": 1, "extra": "x"}, seed=1)
        b = engine.start("seeded", {"extra": "x", "value": 1}, seed=1)
        self.assertEqual(a.run.input_digest, b.run.input_digest)


class TestTracing(unittest.TestCase):
    def setUp(self) -> None:
        self.engine, self.uow, self.workflows, _ = build_engine()
        self.workflows.register(_linear_workflow())

    def test_a_run_is_bracketed_by_start_and_success_events(self) -> None:
        result = self.engine.start("arithmetic", {"value": 1})
        types = [e.type for e in self.uow.traces.list_events(result.run.id)]

        self.assertIs(types[0], TraceEventType.RUN_STARTED)
        self.assertIs(types[-1], TraceEventType.RUN_SUCCEEDED)

    def test_every_step_produces_a_start_and_an_outcome(self) -> None:
        result = self.engine.start("arithmetic", {"value": 1})
        events = self.uow.traces.list_events(result.run.id)

        for step in ("double", "add_ten"):
            with self.subTest(step=step):
                step_events = [e for e in events if e.name == step]
                self.assertEqual(
                    [e.type for e in step_events],
                    [TraceEventType.STEP_STARTED, TraceEventType.STEP_SUCCEEDED],
                )

    def test_the_trace_records_the_seed_for_reproduction(self) -> None:
        result = self.engine.start("arithmetic", {"value": 1}, seed=7)
        start = self.uow.traces.list_events(result.run.id)[0]
        self.assertEqual(start.attributes["seed"], 7)

    def test_trace_sequence_has_no_gaps(self) -> None:
        result = self.engine.start("arithmetic", {"value": 1})
        sequences = [e.sequence for e in self.uow.traces.list_events(result.run.id)]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))


class TestFailureHandling(unittest.TestCase):
    def setUp(self) -> None:
        self.engine, self.uow, self.workflows, _ = build_engine()

        def explode(context: WorkflowContext) -> dict:
            raise RuntimeError("the segmentation adapter timed out")

        self.workflows.register(
            WorkflowDefinition(
                name="fragile",
                version="1.0.0",
                description="",
                steps=(
                    WorkflowStep("first", _double),
                    WorkflowStep("explode", explode),
                    WorkflowStep("never_reached", _add_ten),
                ),
                required_inputs=("value",),
            )
        )

    def test_a_failing_step_fails_the_run_without_raising(self) -> None:
        # The engine returns a failed run rather than propagating, so the caller
        # always gets a trace id and a persisted record.
        result = self.engine.start("fragile", {"value": 2})

        self.assertIs(result.status, RunStatus.FAILED)
        self.assertEqual(result.run.error_code, "step_failed")
        self.assertIn("timed out", result.run.error_message)

    def test_the_run_records_how_far_it_got(self) -> None:
        result = self.engine.start("fragile", {"value": 2})
        self.assertEqual(result.run.completed_steps, ("first",))
        self.assertEqual(result.state["value"], 4, "work done before the failure is kept")

    def test_later_steps_do_not_run(self) -> None:
        result = self.engine.start("fragile", {"value": 2})
        names = {e.name for e in self.uow.traces.list_events(result.run.id)}
        self.assertNotIn("never_reached", names)

    def test_the_failure_is_traced(self) -> None:
        result = self.engine.start("fragile", {"value": 2})
        events = self.uow.traces.list_events(result.run.id)
        self.assertIs(events[-1].type, TraceEventType.RUN_FAILED)
        self.assertEqual(events[-1].error_code, "step_failed")

    def test_a_workflow_error_keeps_its_own_code(self) -> None:
        def refuse(context: WorkflowContext) -> dict:
            raise WorkflowError("input volume is not readable", code="unreadable_volume")

        self.workflows.register(
            WorkflowDefinition(
                name="refusing",
                version="1.0.0",
                description="",
                steps=(WorkflowStep("refuse", refuse),),
            )
        )
        result = self.engine.start("refusing", {})
        self.assertEqual(result.run.error_code, "unreadable_volume")


class TestHumanApproval(unittest.TestCase):
    """ADR-006 and HUMAN_APPROVAL.md: review is a node, so runs must pause."""

    def setUp(self) -> None:
        self.engine, self.uow, self.workflows, _ = build_engine()
        self.executed: list[str] = []

        def analyse(context: WorkflowContext) -> dict:
            self.executed.append("analyse")
            return {"value": context.require("value") * 3}

        def review(context: WorkflowContext) -> dict:
            self.executed.append("review")
            return {"reviewed": True}

        def publish(context: WorkflowContext) -> dict:
            self.executed.append("publish")
            return {"published": True}

        self.workflows.register(
            WorkflowDefinition(
                name="reviewed",
                version="1.0.0",
                description="",
                steps=(
                    WorkflowStep("analyse", analyse),
                    WorkflowStep("review", review, requires_approval=True),
                    WorkflowStep("publish", publish),
                ),
                required_inputs=("value",),
                produces_report=True,
            )
        )

    def test_the_run_pauses_before_the_review_step(self) -> None:
        result = self.engine.start("reviewed", {"value": 2})

        self.assertIs(result.status, RunStatus.AWAITING_APPROVAL)
        self.assertEqual(result.run.paused_at_step, "review")
        self.assertEqual(self.executed, ["analyse"], "nothing past the gate may run")

    def test_pausing_is_traced_as_an_approval_request(self) -> None:
        result = self.engine.start("reviewed", {"value": 2})
        types = [e.type for e in self.uow.traces.list_events(result.run.id)]
        self.assertIn(TraceEventType.APPROVAL_REQUESTED, types)

    def test_resuming_with_approval_continues_from_the_checkpoint(self) -> None:
        paused = self.engine.start("reviewed", {"value": 2})
        state = dict(paused.state)
        state["__approvals__"] = {"review": "dr-reviewer"}

        resumed = self.engine.resume(paused.run.id, state=state)

        self.assertIs(resumed.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.executed, ["analyse", "review", "publish"])
        self.assertEqual(resumed.state["value"], 6, "earlier work was not redone")

    def test_the_resumed_trace_continues_rather_than_restarting(self) -> None:
        paused = self.engine.start("reviewed", {"value": 2})
        before = len(self.uow.traces.list_events(paused.run.id))

        state = dict(paused.state)
        state["__approvals__"] = {"review": "dr-reviewer"}
        resumed = self.engine.resume(paused.run.id, state=state)

        sequences = [e.sequence for e in self.uow.traces.list_events(resumed.run.id)]
        self.assertEqual(sequences, sorted(sequences), "the trace stays ordered")
        self.assertEqual(len(sequences), len(set(sequences)), "no duplicate sequence numbers")
        self.assertGreater(len(sequences), before)

    def test_a_step_can_request_approval_from_inside_itself(self) -> None:
        def ask(context: WorkflowContext) -> dict:
            context.request_approval("uncertainty above the review threshold")
            return {}

        self.workflows.register(
            WorkflowDefinition(
                name="self_pausing",
                version="1.0.0",
                description="",
                steps=(WorkflowStep("ask", ask),),
            )
        )
        result = self.engine.start("self_pausing", {})
        self.assertIs(result.status, RunStatus.AWAITING_APPROVAL)

    def test_only_a_paused_run_can_be_resumed(self) -> None:
        self.workflows.register(_linear_workflow())
        finished = self.engine.start("arithmetic", {"value": 1})
        with self.assertRaises(WorkflowError) as ctx:
            self.engine.resume(finished.run.id)
        self.assertEqual(ctx.exception.code, "run_not_paused")

    def test_resuming_an_unknown_run_raises(self) -> None:
        with self.assertRaises(WorkflowNotFoundError):
            self.engine.resume("run_nope")


class TestDefinitionValidation(unittest.TestCase):
    def test_a_workflow_needs_at_least_one_step(self) -> None:
        with self.assertRaises(ValidationError):
            WorkflowDefinition(name="empty", version="1.0.0", description="", steps=())

    def test_duplicate_step_names_are_refused(self) -> None:
        with self.assertRaises(ValidationError):
            WorkflowDefinition(
                name="dup",
                version="1.0.0",
                description="",
                steps=(WorkflowStep("s", _double), WorkflowStep("s", _add_ten)),
            )

    def test_a_report_producing_workflow_must_contain_a_review_step(self) -> None:
        # This is the safety rule we refuse to leave to reviewer memory.
        with self.assertRaises(ValidationError) as ctx:
            WorkflowDefinition(
                name="unsafe",
                version="1.0.0",
                description="",
                steps=(WorkflowStep("report", _double),),
                produces_report=True,
            )
        self.assertIn("human review", str(ctx.exception))

    def test_the_same_workflow_version_cannot_be_registered_twice(self) -> None:
        registry = WorkflowRegistry()
        registry.register(_linear_workflow())
        with self.assertRaises(ValidationError):
            registry.register(_linear_workflow())

    def test_two_versions_coexist_and_the_newest_wins_by_default(self) -> None:
        registry = WorkflowRegistry()
        registry.register(_linear_workflow(version="1.0.0"))
        registry.register(_linear_workflow(version="2.0.0"))
        self.assertEqual(registry.get("arithmetic").version, "2.0.0")
        self.assertEqual(registry.get("arithmetic", "1.0.0").version, "1.0.0")


class TestContext(unittest.TestCase):
    def setUp(self) -> None:
        self.engine, self.uow, self.workflows, _ = build_engine()

    def test_require_reports_what_was_available(self) -> None:
        def needy(context: WorkflowContext) -> dict:
            context.require("absent")
            return {}

        self.workflows.register(
            WorkflowDefinition(
                name="needy", version="1.0.0", description="", steps=(WorkflowStep("needy", needy),)
            )
        )
        result = self.engine.start("needy", {"present": 1})
        self.assertIs(result.status, RunStatus.FAILED)
        self.assertIn("absent", result.run.error_message)

    def test_a_step_can_create_a_traced_artifact(self) -> None:
        def emit(context: WorkflowContext) -> dict:
            artifact = context.artifacts.create_json(
                payload={"ok": True},
                creator="emit",
                run_id=context.run.id,
                type=ArtifactType.EVALUATION,
            )
            return {"artifact_id": artifact.id}

        self.workflows.register(
            WorkflowDefinition(
                name="emitter", version="1.0.0", description="", steps=(WorkflowStep("emit", emit),)
            )
        )
        result = self.engine.start("emitter", {})
        self.assertIs(result.status, RunStatus.SUCCEEDED)

        artifacts = self.uow.artifacts.list_artifacts(run_id=result.run.id)
        self.assertEqual(len(artifacts), 1)
        types = [e.type for e in self.uow.traces.list_events(result.run.id)]
        self.assertIn(TraceEventType.ARTIFACT_CREATED, types)

    def test_a_step_reaches_a_model_only_through_a_traced_tool_call(self) -> None:
        _, _, workflows, capabilities = build_engine(uow=self.uow)
        capabilities.register(
            Capability(
                name="demo.measure",
                version="0.1.0",
                description="",
                handler=lambda value: {"measured": value * 2},
                inputs=(IOField("value", "integer"),),
                model_adapter="synthetic@0.1.0",
            )
        )

        def measure(context: WorkflowContext) -> dict:
            return context.tools.call("demo.measure", {"value": context.require("value")})

        self.workflows.register(
            WorkflowDefinition(
                name="measuring",
                version="1.0.0",
                description="",
                steps=(WorkflowStep("measure", measure),),
                required_inputs=("value",),
            )
        )
        engine, uow, *_ = build_engine(workflows=self.workflows, capabilities=capabilities, uow=self.uow)
        result = engine.start("measuring", {"value": 3})

        self.assertEqual(result.state["measured"], 6)
        tool_events = [
            e for e in uow.traces.list_events(result.run.id) if e.type is TraceEventType.TOOL_CALLED
        ]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0].attributes["model_adapter"], "synthetic@0.1.0")


if __name__ == "__main__":
    unittest.main()
