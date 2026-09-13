"""Core entities: identity, versioning, provenance and the safety invariants."""

from __future__ import annotations

import unittest

from tests.support import ROOT  # noqa: F401  (ensures src/ is importable)

from medagentos.core.entities import (
    Artifact,
    ArtifactType,
    CaseStatus,
    Evidence,
    Finding,
    MedicalCase,
    Report,
    RunStatus,
    WorkflowRun,
)
from medagentos.core.ids import digest_json, new_id


class TestIdentifiers(unittest.TestCase):
    def test_ids_are_prefixed_and_unique(self) -> None:
        ids = {new_id("run") for _ in range(500)}
        self.assertEqual(len(ids), 500, "identifiers must not collide")
        self.assertTrue(all(i.startswith("run_") for i in ids))

    def test_ids_sort_by_creation_order(self) -> None:
        # The timestamp component must dominate, so a trace read in id order
        # reads in roughly chronological order.
        first = new_id("run")
        import time

        time.sleep(0.002)
        second = new_id("run")
        self.assertLess(first, second)

    def test_json_digest_ignores_key_order(self) -> None:
        self.assertEqual(digest_json({"a": 1, "b": 2}), digest_json({"b": 2, "a": 1}))

    def test_json_digest_distinguishes_values(self) -> None:
        self.assertNotEqual(digest_json({"a": 1}), digest_json({"a": 2}))

    def test_empty_prefix_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            new_id("")


class TestCase(unittest.TestCase):
    def test_status_transition_is_a_copy(self) -> None:
        case = MedicalCase(subject_ref="anon-1")
        closed = case.with_status(CaseStatus.CLOSED)
        self.assertIs(case.status, CaseStatus.OPEN, "entities are immutable")
        self.assertIs(closed.status, CaseStatus.CLOSED)
        self.assertEqual(case.id, closed.id)
        self.assertGreaterEqual(closed.updated_at, case.updated_at)


class TestArtifact(unittest.TestCase):
    def test_new_version_records_lineage(self) -> None:
        first = Artifact(type=ArtifactType.MASK, creator="seg@1", location="memory://a", digest="sha256:a")
        second = first.next_version(location="memory://b", digest="sha256:b", creator="seg@2")

        self.assertEqual(second.version, 2)
        self.assertIn(first.id, second.parent_ids)
        self.assertNotEqual(second.id, first.id, "a new version is a new artifact")
        self.assertIs(second.type, first.type)

    def test_lineage_accumulates_across_versions(self) -> None:
        a = Artifact(creator="x", location="memory://a", digest="sha256:a")
        b = a.next_version(location="memory://b", digest="sha256:b", creator="y")
        c = b.next_version(location="memory://c", digest="sha256:c", creator="z")
        self.assertEqual(c.parent_ids, (a.id, b.id))
        self.assertEqual(c.version, 3)


class TestFinding(unittest.TestCase):
    def test_confidence_must_be_a_probability(self) -> None:
        for bad in (-0.1, 1.1, 42.0):
            with self.subTest(confidence=bad), self.assertRaises(ValueError):
                Finding(confidence=bad)

    def test_confidence_bounds_are_inclusive(self) -> None:
        for good in (0.0, 0.5, 1.0):
            with self.subTest(confidence=good):
                Finding(confidence=good)

    def test_a_finding_without_evidence_is_unsupported(self) -> None:
        self.assertFalse(Finding(code="x").is_supported)
        supported = Finding(code="x", evidence=(Evidence(kind="measurement", source="seg@1"),))
        self.assertTrue(supported.is_supported)


class TestReport(unittest.TestCase):
    def test_a_report_is_not_approved_until_a_human_approves(self) -> None:
        report = Report(title="Brain MRI")
        self.assertFalse(report.is_approved)

        approved = report.approve("dr-reviewer")
        self.assertTrue(approved.is_approved)
        self.assertEqual(approved.approved_by, "dr-reviewer")
        self.assertIsNotNone(approved.approved_at)
        self.assertFalse(report.is_approved, "approval must not mutate the original")

    def test_approval_requires_a_named_reviewer(self) -> None:
        with self.assertRaises(ValueError):
            Report(title="x").approve("")


class TestWorkflowRun(unittest.TestCase):
    def test_terminal_statuses(self) -> None:
        self.assertTrue(RunStatus.SUCCEEDED.is_terminal)
        self.assertTrue(RunStatus.FAILED.is_terminal)
        self.assertFalse(RunStatus.AWAITING_APPROVAL.is_terminal)
        self.assertFalse(RunStatus.RUNNING.is_terminal)

    def test_input_digest_is_stable_across_key_order(self) -> None:
        a = WorkflowRun(inputs={"image": "x", "seed": 1})
        b = WorkflowRun(inputs={"seed": 1, "image": "x"})
        self.assertEqual(a.input_digest, b.input_digest)

    def test_duration_is_unknown_until_finished(self) -> None:
        run = WorkflowRun()
        self.assertIsNone(run.duration_ms)


if __name__ == "__main__":
    unittest.main()
