"""Workflow Runtime.

WORKFLOW_ENGINE.md makes the workflow the central abstraction and asks for
deterministic execution, trace generation, error handling and versioning. This
module provides all four and nothing medical.

A workflow is an ordered list of steps. Each step receives a
``WorkflowContext`` and returns a mapping merged into the run state. Steps are
plain functions, so a plugin author needs no framework knowledge to write one,
and a test can call a step directly.

Execution is deterministic: the same definition version, the same inputs and the
same seed produce the same sequence of steps and the same state digests.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .artifacts import ArtifactManager
from .capabilities import CapabilityRegistry, ToolExecutor
from .entities import JsonDict, RunStatus, TraceEventType, WorkflowRun, now
from .errors import (
    ApprovalRequired,
    StepError,
    ValidationError,
    WorkflowError,
    WorkflowNotFoundError,
)
from .ids import digest_json
from .trace import TraceEngine

StepFunction = Callable[["WorkflowContext"], Mapping[str, Any] | None]


@dataclass
class WorkflowContext:
    """Everything a step is allowed to touch.

    A step gets its inputs, the accumulated state, a tool executor, an artifact
    manager and a seeded RNG. It does not get a database handle or an HTTP
    client: anything a step needs from the outside world arrives through a
    capability, so that it is contract-checked and traced.
    """

    run: WorkflowRun
    state: dict[str, Any]
    tools: ToolExecutor
    artifacts: ArtifactManager
    trace: TraceEngine
    rng: random.Random
    _pause_reason: str | None = field(default=None, init=False, repr=False)

    @property
    def inputs(self) -> JsonDict:
        return self.run.inputs

    def require(self, key: str) -> Any:
        """Read a state or input value that must exist, with a useful error if it does not."""
        if key in self.state:
            return self.state[key]
        if key in self.run.inputs:
            return self.run.inputs[key]
        raise ValidationError(
            f"the workflow state has no value for {key!r}",
            details={"available": sorted({*self.state, *self.run.inputs})},
        )

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.state:
            return self.state[key]
        return self.run.inputs.get(key, default)

    def request_approval(self, reason: str = "human approval required") -> None:
        """Pause the run here and wait for a human (ADR-006, HUMAN_APPROVAL.md)."""
        raise ApprovalRequired(step=self.state.get("__current_step__", ""), message=reason)


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    name: str
    run: StepFunction
    description: str = ""
    requires_approval: bool = False
    """When true the runtime pauses *before* this step until a human approves."""


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    """A named, versioned sequence of steps.

    Versioning is mandatory: a run records the definition version it executed, so
    a trace stays interpretable after the workflow changes.
    """

    name: str
    version: str
    description: str
    steps: tuple[WorkflowStep, ...]
    plugin: str = ""
    required_inputs: tuple[str, ...] = ()
    produces_report: bool = False
    """Whether this workflow emits a report. If it does, it must contain a human
    review step — enforced in ``__post_init__``, because SAFETY_MODEL.md is not
    a guideline we want to rely on people remembering."""

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValidationError(f"workflow {self.name!r} has no steps")
        names = [step.name for step in self.steps]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValidationError(
                f"workflow {self.name!r} has duplicate step names: {sorted(duplicates)}"
            )
        if self.produces_report and not any(step.requires_approval for step in self.steps):
            raise ValidationError(
                f"workflow {self.name!r} produces a report but has no human review step; "
                "ADR-006 requires human review before a report is final"
            )

    @property
    def qualified_name(self) -> str:
        return f"{self.name}@{self.version}"

    def step(self, name: str) -> WorkflowStep:
        for step in self.steps:
            if step.name == name:
                return step
        raise WorkflowNotFoundError(f"workflow {self.name!r} has no step {name!r}")


class WorkflowRegistry:
    """Workflows available to the runtime, keyed by name and version."""

    def __init__(self) -> None:
        self._by_name: dict[str, dict[str, WorkflowDefinition]] = {}

    def register(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        versions = self._by_name.setdefault(definition.name, {})
        if definition.version in versions:
            raise ValidationError(f"workflow {definition.qualified_name} is already registered")
        versions[definition.version] = definition
        return definition

    def get(self, name: str, version: str | None = None) -> WorkflowDefinition:
        versions = self._by_name.get(name)
        if not versions:
            raise WorkflowNotFoundError(f"no workflow named {name!r} is registered")
        if version is None:
            from .capabilities import _version_key

            return versions[max(versions, key=_version_key)]
        if version not in versions:
            raise WorkflowNotFoundError(
                f"workflow {name!r} has no version {version!r}",
                details={"available": sorted(versions)},
            )
        return versions[version]

    def list(self) -> list[WorkflowDefinition]:
        return sorted(
            (d for versions in self._by_name.values() for d in versions.values()),
            key=lambda d: (d.name, d.version),
        )

    def describe(self) -> list[JsonDict]:
        return [
            {
                "name": d.name,
                "version": d.version,
                "description": d.description,
                "plugin": d.plugin,
                "required_inputs": list(d.required_inputs),
                "produces_report": d.produces_report,
                "steps": [
                    {"name": s.name, "description": s.description,
                     "requires_approval": s.requires_approval}
                    for s in d.steps
                ],
            }
            for d in self.list()
        ]


@dataclass(frozen=True, slots=True)
class RunResult:
    """Outcome of one execution attempt."""

    run: WorkflowRun
    state: dict[str, Any]

    @property
    def status(self) -> RunStatus:
        return self.run.status

    @property
    def is_paused(self) -> bool:
        return self.run.status is RunStatus.AWAITING_APPROVAL


class WorkflowEngine:
    """Executes workflow definitions deterministically and traces everything.

    The engine is synchronous by design (D-05). Long-running work belongs to the
    worker; workflow *semantics* stay sequential and reproducible.
    """

    def __init__(
        self,
        *,
        workflows: WorkflowRegistry,
        capabilities: CapabilityRegistry,
        artifact_manager_factory: Callable[[TraceEngine], ArtifactManager],
        trace_engine_factory: Callable[[str], TraceEngine],
        run_repository: Any,
    ) -> None:
        self._workflows = workflows
        self._capabilities = capabilities
        self._artifact_manager_factory = artifact_manager_factory
        self._trace_engine_factory = trace_engine_factory
        self._runs = run_repository

    def start(
        self,
        workflow_name: str,
        inputs: JsonDict,
        *,
        version: str | None = None,
        case_id: str | None = None,
        seed: int = 0,
    ) -> RunResult:
        definition = self._workflows.get(workflow_name, version)
        self._check_required_inputs(definition, inputs)

        run = WorkflowRun(
            workflow_name=definition.name,
            workflow_version=definition.version,
            case_id=case_id,
            status=RunStatus.PENDING,
            seed=seed,
            inputs=dict(inputs),
        )
        run = self._runs.save_run(run)
        return self._execute(definition, run, state={})

    def resume(self, run_id: str, *, state: dict[str, Any] | None = None) -> RunResult:
        """Continue a run that paused for human approval.

        Resumption replays from the recorded checkpoint rather than from the
        beginning, which is what makes review a workflow node instead of a
        reason to run everything twice (D-08).
        """
        run = self._runs.get_run(run_id)
        if run is None:
            raise WorkflowNotFoundError(f"run {run_id!r} does not exist")
        if run.status is not RunStatus.AWAITING_APPROVAL:
            raise WorkflowError(
                f"run {run_id!r} is {run.status.value}, only a paused run can be resumed",
                code="run_not_paused",
            )
        definition = self._workflows.get(run.workflow_name, run.workflow_version)
        resumed = replace(run, status=RunStatus.RUNNING, paused_at_step=None)
        return self._execute(definition, resumed, state=dict(state or run.outputs))

    # -- execution ---------------------------------------------------------

    def _execute(
        self, definition: WorkflowDefinition, run: WorkflowRun, *, state: dict[str, Any]
    ) -> RunResult:
        trace = self._trace_engine_factory(run.id)
        # Restore the sequence counter so a resumed run continues its trace
        # rather than restarting the numbering and corrupting the ordering.
        existing = len(trace.events())
        trace._sequence = existing  # noqa: SLF001 - engine owns the trace lifecycle

        run = replace(run, status=RunStatus.RUNNING)
        self._runs.save_run(run)
        trace.record(
            TraceEventType.RUN_STARTED,
            definition.qualified_name,
            input_digest=run.input_digest,
            seed=run.seed,
            case_id=run.case_id,
            resumed=existing > 0,
        )

        context = WorkflowContext(
            run=run,
            state=state,
            tools=ToolExecutor(self._capabilities, trace),
            artifacts=self._artifact_manager_factory(trace),
            trace=trace,
            # Seeded per run, so a re-run with the same seed makes the same choices.
            rng=random.Random(run.seed),
        )

        completed = list(run.completed_steps)
        for step in definition.steps:
            if step.name in completed:
                continue

            if step.requires_approval and not self._approval_granted(state, step.name):
                trace.record(
                    TraceEventType.APPROVAL_REQUESTED,
                    step.name,
                    reason="workflow step requires human review",
                )
                paused = replace(
                    run,
                    status=RunStatus.AWAITING_APPROVAL,
                    paused_at_step=step.name,
                    completed_steps=tuple(completed),
                    outputs=_serialisable(state),
                )
                return RunResult(self._runs.save_run(paused), state)

            state["__current_step__"] = step.name
            try:
                with trace.step(step.name, inputs={"state_digest": digest_json(_serialisable(state))}) as span:
                    produced = step.run(context)
                    if produced:
                        state.update(produced)
                    span.set_output({"state_digest": digest_json(_serialisable(state))})
            except ApprovalRequired:
                trace.record(TraceEventType.APPROVAL_REQUESTED, step.name)
                paused = replace(
                    run,
                    status=RunStatus.AWAITING_APPROVAL,
                    paused_at_step=step.name,
                    completed_steps=tuple(completed),
                    outputs=_serialisable(state),
                )
                return RunResult(self._runs.save_run(paused), state)
            except WorkflowError as exc:
                return RunResult(self._fail(run, trace, definition, completed, state, exc), state)
            except Exception as exc:
                wrapped = StepError(step.name, f"{type(exc).__name__}: {exc}", trace_id=run.id)
                return RunResult(
                    self._fail(run, trace, definition, completed, state, wrapped), state
                )

            completed.append(step.name)

        state.pop("__current_step__", None)
        finished = replace(
            run,
            status=RunStatus.SUCCEEDED,
            completed_steps=tuple(completed),
            outputs=_serialisable(state),
            finished_at=now(),
        )
        finished = self._runs.save_run(finished)
        trace.record(
            TraceEventType.RUN_SUCCEEDED,
            definition.qualified_name,
            duration_ms=finished.duration_ms,
            output_digest=digest_json(finished.outputs),
            steps=len(completed),
        )
        return RunResult(finished, state)

    def _fail(
        self,
        run: WorkflowRun,
        trace: TraceEngine,
        definition: WorkflowDefinition,
        completed: list[str],
        state: dict[str, Any],
        error: WorkflowError,
    ) -> WorkflowRun:
        if error.trace_id is None:
            error.with_trace(run.id)
        failed = replace(
            run,
            status=RunStatus.FAILED,
            completed_steps=tuple(completed),
            outputs=_serialisable(state),
            error_code=error.code,
            error_message=error.message,
            finished_at=now(),
        )
        failed = self._runs.save_run(failed)
        trace.record(
            TraceEventType.RUN_FAILED,
            definition.qualified_name,
            duration_ms=failed.duration_ms,
            error_code=error.code,
            error_message=error.message,
        )
        return failed

    @staticmethod
    def _approval_granted(state: Mapping[str, Any], step_name: str) -> bool:
        approvals = state.get("__approvals__") or {}
        return bool(isinstance(approvals, Mapping) and approvals.get(step_name))

    @staticmethod
    def _check_required_inputs(definition: WorkflowDefinition, inputs: Mapping[str, Any]) -> None:
        missing = [name for name in definition.required_inputs if name not in inputs]
        if missing:
            raise ValidationError(
                f"workflow {definition.qualified_name} is missing required inputs: "
                f"{', '.join(missing)}",
                details={"missing": missing, "required": list(definition.required_inputs)},
            )


def _serialisable(state: Mapping[str, Any]) -> JsonDict:
    """Project run state down to something a database column can hold.

    Steps may keep arbitrary Python objects in state; the persisted ``outputs``
    keep only what survives a round trip, with everything else reduced to a
    digest so the record stays complete without becoming a pickle store.
    """
    result: JsonDict = {}
    for key, value in state.items():
        if key.startswith("__"):
            continue
        if isinstance(value, (str, int, float, bool, type(None))):
            result[key] = value
        elif isinstance(value, (list, tuple, dict)):
            try:
                digest_json(value)
            except (TypeError, ValueError):
                result[key] = {"$unserialisable": type(value).__name__}
            else:
                result[key] = list(value) if isinstance(value, tuple) else value
        else:
            result[key] = {"$ref": type(value).__name__, "$digest": digest_json(value)}
    return result
