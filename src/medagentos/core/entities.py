"""Core domain entities.

These are the seven entities named in ``CORE_DESIGN.md`` plus the trace event
that ``ADR-004`` promotes to a first-class concern. They are plain frozen
dataclasses: no ORM, no validation framework, no medical semantics. A brain MRI
and a chest X-ray are both just a ``Study`` with a different modality string.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .ids import digest_json, new_id

JsonDict = dict[str, Any]


def now() -> float:
    """Wall-clock time as a UNIX timestamp. One definition, so tests can patch one place."""
    return time.time()


_now = now  # internal alias used by the dataclass default factories below


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class CaseStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    AWAITING_REVIEW = "awaiting_review"
    CLOSED = "closed"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.SUCCEEDED, RunStatus.FAILED)


class ArtifactType(str, Enum):
    """The artifact kinds named in ARTIFACT_SYSTEM.md.

    Deliberately generic: ``IMAGE`` covers an MRI volume and a histology slide
    alike. Anything more specific is plugin metadata, not a core concept.
    """

    IMAGE = "image"
    MASK = "mask"
    REPORT = "report"
    EMBEDDING = "embedding"
    EVALUATION = "evaluation"
    DOCUMENT = "document"


class TraceEventType(str, Enum):
    RUN_STARTED = "run.started"
    STEP_STARTED = "step.started"
    STEP_SUCCEEDED = "step.succeeded"
    STEP_FAILED = "step.failed"
    TOOL_CALLED = "tool.called"
    TOOL_SUCCEEDED = "tool.succeeded"
    TOOL_FAILED = "tool.failed"
    ARTIFACT_CREATED = "artifact.created"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"


class Severity(str, Enum):
    """How much attention a finding demands.

    This is an operational triage hint, never a diagnosis or an acuity score.
    """

    INFORMATIONAL = "informational"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MedicalCase:
    """A unit of work about one subject. Holds no clinical interpretation."""

    id: str = field(default_factory=lambda: new_id("case"))
    subject_ref: str = ""
    """Opaque reference to the subject. MedAgentOS never stores identifying data."""
    status: CaseStatus = CaseStatus.OPEN
    metadata: JsonDict = field(default_factory=dict)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)

    def with_status(self, status: CaseStatus) -> MedicalCase:
        return replace(self, status=status, updated_at=_now())


@dataclass(frozen=True, slots=True)
class Study:
    """One acquisition belonging to a case, e.g. a T1 MRI series."""

    id: str = field(default_factory=lambda: new_id("study"))
    case_id: str = ""
    modality: str = ""
    """Free-form modality string such as ``MR`` or ``CT``. Core does not validate it;
    a plugin that cares about modality validates it against its own contract."""
    description: str = ""
    metadata: JsonDict = field(default_factory=dict)
    created_at: float = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class Artifact:
    """Anything a workflow produced or consumed.

    Carries the six fields ARTIFACT_SYSTEM.md requires (id, type, version,
    creator, metadata, location) plus the content digest that makes a run
    reproducible and the run that produced it, which makes it traceable.
    """

    id: str = field(default_factory=lambda: new_id("art"))
    type: ArtifactType = ArtifactType.DOCUMENT
    version: int = 1
    creator: str = ""
    """Who produced it: a step name, an adapter id, or a user id."""
    location: str = ""
    """URI in the object store. Empty only for an artifact still being built."""
    digest: str = ""
    case_id: str | None = None
    run_id: str | None = None
    parent_ids: tuple[str, ...] = ()
    """Artifacts this one was derived from. This is the provenance chain."""
    metadata: JsonDict = field(default_factory=dict)
    created_at: float = field(default_factory=_now)

    def next_version(self, *, location: str, digest: str, creator: str) -> Artifact:
        """Return the successor version of this artifact, keeping it as a parent."""
        return replace(
            self,
            id=new_id("art"),
            version=self.version + 1,
            location=location,
            digest=digest,
            creator=creator,
            parent_ids=(*self.parent_ids, self.id),
            created_at=_now(),
        )


@dataclass(frozen=True, slots=True)
class Evidence:
    """Support for a finding: a measurement, a region, or a retrieved reference.

    SAFETY_MODEL.md requires provenance for every output. Evidence is how a
    finding answers "where did this come from?" in a machine-readable way.
    """

    id: str = field(default_factory=lambda: new_id("ev"))
    kind: str = ""
    """e.g. ``measurement``, ``region``, ``literature``, ``prior_study``."""
    summary: str = ""
    source: str = ""
    """Adapter id, artifact id, or citation. Never empty for a published finding."""
    artifact_id: str | None = None
    data: JsonDict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Finding:
    """A structured observation produced by a medical capability.

    A finding is never a diagnosis. It records what was observed, how confident
    the producing model was, what supports it, and what could not be assessed.
    """

    id: str = field(default_factory=lambda: new_id("find"))
    case_id: str = ""
    run_id: str = ""
    code: str = ""
    """Plugin-defined identifier, e.g. ``lesion.volume.elevated``."""
    description: str = ""
    severity: Severity = Severity.INFORMATIONAL
    confidence: float = 0.0
    """Producing model's confidence in [0, 1]. Required by SAFETY_MODEL.md."""
    evidence: tuple[Evidence, ...] = ()
    limitations: tuple[str, ...] = ()
    """What this finding cannot tell you. Required by SAFETY_MODEL.md."""
    produced_by: str = ""
    """Capability id and version, e.g. ``brain_mri.segmentation@0.1.0``."""
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence}")

    @property
    def is_supported(self) -> bool:
        """Whether the finding carries at least one piece of evidence.

        The evaluation engine counts unsupported findings as a trust defect.
        """
        return bool(self.evidence)


@dataclass(frozen=True, slots=True)
class Report:
    """A human-readable summary of a run, pending or carrying human approval."""

    id: str = field(default_factory=lambda: new_id("rep"))
    case_id: str = ""
    run_id: str = ""
    title: str = ""
    body: str = ""
    findings: tuple[Finding, ...] = ()
    limitations: tuple[str, ...] = ()
    approved_by: str | None = None
    """``None`` until a human approves. ADR-006: no report is final without this."""
    approved_at: float | None = None
    created_at: float = field(default_factory=_now)

    @property
    def is_approved(self) -> bool:
        return self.approved_by is not None

    def approve(self, reviewer: str) -> Report:
        if not reviewer:
            raise ValueError("an approval must name its reviewer")
        return replace(self, approved_by=reviewer, approved_at=_now())


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One append-only record of something the runtime did.

    Events carry digests and references, never payloads: a trace must stay cheap
    enough to keep forever, and imaging payloads belong in object storage.
    """

    id: str = field(default_factory=lambda: new_id("tev"))
    run_id: str = ""
    type: TraceEventType = TraceEventType.STEP_STARTED
    name: str = ""
    """Step, tool or capability name this event concerns."""
    sequence: int = 0
    """Position within the run. Monotonic; makes ordering independent of clock skew."""
    timestamp: float = field(default_factory=_now)
    duration_ms: float | None = None
    input_digest: str | None = None
    output_digest: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    attributes: JsonDict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    """One execution of one version of one workflow."""

    id: str = field(default_factory=lambda: new_id("run"))
    workflow_name: str = ""
    workflow_version: str = ""
    case_id: str | None = None
    status: RunStatus = RunStatus.PENDING
    seed: int = 0
    """Explicit seed recorded for reproducibility (D-09)."""
    inputs: JsonDict = field(default_factory=dict)
    outputs: JsonDict = field(default_factory=dict)
    completed_steps: tuple[str, ...] = ()
    """Checkpoint: which steps already ran. Lets an approval resume a paused run."""
    paused_at_step: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: float = field(default_factory=_now)
    finished_at: float | None = None

    @property
    def input_digest(self) -> str:
        return digest_json(self.inputs)

    @property
    def duration_ms(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at) * 1000
