"""Ports: the interfaces Core owns and infrastructure implements.

SERVICE_ARCHITECTURE.md fixes the dependency direction as
``API -> Services -> Core Interfaces -> Infrastructure``. Those "core
interfaces" are these protocols. Core depends on the protocol; ``medagentos.infra``
depends on Core to satisfy it. Nothing in Core ever imports an adapter.

They are ``Protocol`` classes rather than abstract base classes so an adapter
never has to import Core to be usable by it — structural typing keeps even the
inheritance graph one-directional.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .entities import (
    Artifact,
    Finding,
    MedicalCase,
    Report,
    Study,
    TraceEvent,
    WorkflowRun,
)


@runtime_checkable
class ObjectStore(Protocol):
    """Byte storage for artifact payloads.

    STORAGE_DESIGN.md: large medical files live here, never in the database.
    Implemented by the local filesystem in development and MinIO/S3 in compose.
    """

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        """Store ``data`` and return its location URI."""
        ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def uri_for(self, key: str) -> str:
        """Return the location URI for ``key`` without touching the payload."""
        ...


@runtime_checkable
class CaseRepository(Protocol):
    def save_case(self, case: MedicalCase) -> MedicalCase: ...

    def get_case(self, case_id: str) -> MedicalCase | None: ...

    def list_cases(self, *, limit: int = 50, offset: int = 0) -> list[MedicalCase]: ...

    def save_study(self, study: Study) -> Study: ...

    def list_studies(self, case_id: str) -> list[Study]: ...


@runtime_checkable
class ArtifactRepository(Protocol):
    def save_artifact(self, artifact: Artifact) -> Artifact: ...

    def get_artifact(self, artifact_id: str) -> Artifact | None: ...

    def list_artifacts(
        self, *, case_id: str | None = None, run_id: str | None = None
    ) -> list[Artifact]: ...


@runtime_checkable
class RunRepository(Protocol):
    def save_run(self, run: WorkflowRun) -> WorkflowRun: ...

    def get_run(self, run_id: str) -> WorkflowRun | None: ...

    def list_runs(self, *, case_id: str | None = None, limit: int = 50) -> list[WorkflowRun]: ...


@runtime_checkable
class TraceRepository(Protocol):
    def append_event(self, event: TraceEvent) -> TraceEvent: ...

    def list_events(self, run_id: str) -> list[TraceEvent]:
        """Return the run's events ordered by ``sequence``."""
        ...


@runtime_checkable
class FindingRepository(Protocol):
    def save_finding(self, finding: Finding) -> Finding: ...

    def list_findings(self, *, case_id: str | None = None, run_id: str | None = None) -> list[Finding]: ...


@runtime_checkable
class ReportRepository(Protocol):
    def save_report(self, report: Report) -> Report: ...

    def get_report(self, report_id: str) -> Report | None: ...

    def list_reports(self, *, case_id: str | None = None, run_id: str | None = None) -> list[Report]: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """The whole persistence surface, so services take one dependency, not seven."""

    cases: CaseRepository
    artifacts: ArtifactRepository
    runs: RunRepository
    traces: TraceRepository
    findings: FindingRepository
    reports: ReportRepository
    objects: ObjectStore


@runtime_checkable
class VectorIndex(Protocol):
    """Optional evidence retrieval.

    VECTOR_SEARCH.md is explicit that this is a capability, not a Core
    dependency: a runtime with no vector index must still execute workflows.
    """

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None: ...

    def search(
        self, collection: str, vector: list[float], *, limit: int = 5
    ) -> list[tuple[str, float, dict]]:
        """Return ``(id, score, payload)`` triples, best match first."""
        ...


@runtime_checkable
class Clock(Protocol):
    """Injectable time source. Deterministic tests need a deterministic clock."""

    def now(self) -> float: ...
