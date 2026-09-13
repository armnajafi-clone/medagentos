"""In-memory adapters.

The default for tests and for the CLI's ``--ephemeral`` mode. They implement the
same ports as the SQLite and PostgreSQL adapters, so a test that passes here is
testing real runtime behaviour rather than a mock's behaviour.

Not thread-safe and not durable, which is exactly what a test wants.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.entities import Artifact, Finding, MedicalCase, Report, Study, TraceEvent, WorkflowRun
from ..core.errors import NotFoundError


class InMemoryObjectStore:
    """Byte storage in a dict. Location URIs use the ``memory://`` scheme."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._content_types: dict[str, str] = {}

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        self._data[key] = bytes(data)
        self._content_types[key] = content_type
        return self.uri_for(key)

    def get(self, key: str) -> bytes:
        try:
            return self._data[key]
        except KeyError:
            raise NotFoundError(f"object {key!r} is not in the store") from None

    def exists(self, key: str) -> bool:
        return key in self._data

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._content_types.pop(key, None)

    def uri_for(self, key: str) -> str:
        return f"memory://{key}"

    def __len__(self) -> int:
        return len(self._data)


class InMemoryRepositories:
    """All seven repositories over plain dicts, satisfying ``UnitOfWork``."""

    def __init__(self) -> None:
        self._cases: dict[str, MedicalCase] = {}
        self._studies: dict[str, Study] = {}
        self._artifacts: dict[str, Artifact] = {}
        self._runs: dict[str, WorkflowRun] = {}
        self._events: dict[str, list[TraceEvent]] = {}
        self._findings: dict[str, Finding] = {}
        self._reports: dict[str, Report] = {}

    # -- cases -------------------------------------------------------------

    def save_case(self, case: MedicalCase) -> MedicalCase:
        self._cases[case.id] = case
        return case

    def get_case(self, case_id: str) -> MedicalCase | None:
        return self._cases.get(case_id)

    def list_cases(self, *, limit: int = 50, offset: int = 0) -> list[MedicalCase]:
        ordered = sorted(self._cases.values(), key=lambda c: c.created_at, reverse=True)
        return ordered[offset : offset + limit]

    def save_study(self, study: Study) -> Study:
        self._studies[study.id] = study
        return study

    def list_studies(self, case_id: str) -> list[Study]:
        return sorted(
            (s for s in self._studies.values() if s.case_id == case_id),
            key=lambda s: s.created_at,
        )

    # -- artifacts ---------------------------------------------------------

    def save_artifact(self, artifact: Artifact) -> Artifact:
        self._artifacts[artifact.id] = artifact
        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        return self._artifacts.get(artifact_id)

    def list_artifacts(
        self, *, case_id: str | None = None, run_id: str | None = None
    ) -> list[Artifact]:
        return sorted(
            (
                a
                for a in self._artifacts.values()
                if (case_id is None or a.case_id == case_id)
                and (run_id is None or a.run_id == run_id)
            ),
            key=lambda a: a.created_at,
        )

    # -- runs --------------------------------------------------------------

    def save_run(self, run: WorkflowRun) -> WorkflowRun:
        self._runs[run.id] = run
        return run

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def list_runs(self, *, case_id: str | None = None, limit: int = 50) -> list[WorkflowRun]:
        ordered = sorted(
            (r for r in self._runs.values() if case_id is None or r.case_id == case_id),
            key=lambda r: r.started_at,
            reverse=True,
        )
        return ordered[:limit]

    # -- traces ------------------------------------------------------------

    def append_event(self, event: TraceEvent) -> TraceEvent:
        self._events.setdefault(event.run_id, []).append(event)
        return event

    def list_events(self, run_id: str) -> list[TraceEvent]:
        return sorted(self._events.get(run_id, []), key=lambda e: e.sequence)

    # -- findings ----------------------------------------------------------

    def save_finding(self, finding: Finding) -> Finding:
        self._findings[finding.id] = finding
        return finding

    def list_findings(
        self, *, case_id: str | None = None, run_id: str | None = None
    ) -> list[Finding]:
        return [
            f
            for f in self._findings.values()
            if (case_id is None or f.case_id == case_id) and (run_id is None or f.run_id == run_id)
        ]

    # -- reports -----------------------------------------------------------

    def save_report(self, report: Report) -> Report:
        self._reports[report.id] = report
        return report

    def get_report(self, report_id: str) -> Report | None:
        return self._reports.get(report_id)

    def list_reports(self, *, case_id: str | None = None, run_id: str | None = None) -> list[Report]:
        return sorted(
            (
                r
                for r in self._reports.values()
                if (case_id is None or r.case_id == case_id)
                and (run_id is None or r.run_id == run_id)
            ),
            key=lambda r: r.created_at,
        )


@dataclass
class InMemoryUnitOfWork:
    """Wires the in-memory repositories into the ``UnitOfWork`` shape."""

    repositories: InMemoryRepositories = field(default_factory=InMemoryRepositories)
    objects: InMemoryObjectStore = field(default_factory=InMemoryObjectStore)

    def __post_init__(self) -> None:
        # One object serves every repository port; the split exists for the
        # type system and for adapters where the backends genuinely differ.
        self.cases = self.repositories
        self.artifacts = self.repositories
        self.runs = self.repositories
        self.traces = self.repositories
        self.findings = self.repositories
        self.reports = self.repositories
