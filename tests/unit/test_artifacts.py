"""Artifact Manager: content addressing, provenance, and the metadata/payload split."""

from __future__ import annotations

import unittest

from tests.support import ROOT  # noqa: F401

from medagentos.core.artifacts import ArtifactManager
from medagentos.core.entities import ArtifactType, TraceEventType
from medagentos.core.errors import NotFoundError
from medagentos.core.trace import TraceEngine
from medagentos.infra.memory import InMemoryUnitOfWork


class TestArtifactManager(unittest.TestCase):
    def setUp(self) -> None:
        self.uow = InMemoryUnitOfWork()
        self.trace = TraceEngine("run_test", self.uow.traces)
        self.manager = ArtifactManager(self.uow.artifacts, self.uow.objects, trace=self.trace)

    def test_create_stores_payload_and_metadata_separately(self) -> None:
        artifact = self.manager.create(
            type=ArtifactType.MASK, data=b"mask-bytes", creator="seg@0.1.0", run_id="run_test"
        )

        self.assertTrue(artifact.location.startswith("memory://artifacts/mask/"))
        self.assertTrue(artifact.digest.startswith("sha256:"))
        self.assertEqual(self.manager.read(artifact.id), b"mask-bytes")
        # DATABASE_DESIGN.md: the record holds a reference, not the payload.
        self.assertNotIn(b"mask-bytes", repr(artifact).encode())

    def test_identical_payloads_share_one_object(self) -> None:
        a = self.manager.create(type=ArtifactType.MASK, data=b"same", creator="x")
        b = self.manager.create(type=ArtifactType.MASK, data=b"same", creator="y")

        self.assertEqual(a.digest, b.digest)
        self.assertEqual(a.location, b.location, "content addressing must deduplicate")
        self.assertNotEqual(a.id, b.id, "but they are distinct artifact records")
        self.assertEqual(len(self.uow.objects), 1)

    def test_different_payloads_get_different_locations(self) -> None:
        a = self.manager.create(type=ArtifactType.MASK, data=b"one", creator="x")
        b = self.manager.create(type=ArtifactType.MASK, data=b"two", creator="x")
        self.assertNotEqual(a.location, b.location)

    def test_creation_is_traced(self) -> None:
        artifact = self.manager.create(type=ArtifactType.REPORT, data=b"# report", creator="rep")
        events = [e for e in self.trace.events() if e.type is TraceEventType.ARTIFACT_CREATED]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].attributes["artifact_id"], artifact.id)
        self.assertEqual(events[0].attributes["size_bytes"], 8)

    def test_json_round_trip_is_stable(self) -> None:
        payload = {"findings": [{"code": "lesion", "volume_ml": 3.5}]}
        artifact = self.manager.create_json(payload=payload, creator="extract")
        self.assertEqual(self.manager.read_json(artifact.id), payload)

        # Key order must not change the digest, or reproducibility is a lie.
        reordered = self.manager.create_json(
            payload={"findings": [{"volume_ml": 3.5, "code": "lesion"}]}, creator="extract"
        )
        self.assertEqual(artifact.digest, reordered.digest)

    def test_new_version_keeps_the_previous_one(self) -> None:
        first = self.manager.create(type=ArtifactType.REPORT, data=b"draft", creator="rep@1")
        second = self.manager.new_version(first.id, data=b"revised", creator="rep@2")

        self.assertEqual(second.version, 2)
        self.assertEqual(self.manager.read(first.id), b"draft", "the old version survives")
        self.assertEqual(self.manager.read(second.id), b"revised")

    def test_new_version_merges_metadata(self) -> None:
        first = self.manager.create(
            type=ArtifactType.REPORT, data=b"a", creator="r", metadata={"kept": 1}
        )
        second = self.manager.new_version(first.id, data=b"b", creator="r", metadata={"added": 2})
        self.assertEqual(second.metadata, {"kept": 1, "added": 2})

    def test_lineage_returns_the_full_provenance_chain(self) -> None:
        first = self.manager.create(type=ArtifactType.IMAGE, data=b"v1", creator="ingest")
        second = self.manager.new_version(first.id, data=b"v2", creator="preprocess")
        third = self.manager.new_version(second.id, data=b"v3", creator="segment")

        chain = self.manager.lineage(third.id)
        self.assertEqual([a.id for a in chain], [first.id, second.id, third.id])

    def test_lineage_follows_derivation_not_only_versioning(self) -> None:
        image = self.manager.create(type=ArtifactType.IMAGE, data=b"mri", creator="ingest")
        mask = self.manager.create(
            type=ArtifactType.MASK, data=b"mask", creator="segment", parent_ids=(image.id,)
        )
        chain = self.manager.lineage(mask.id)
        self.assertEqual([a.id for a in chain], [image.id, mask.id])

    def test_missing_artifact_raises_not_found(self) -> None:
        with self.assertRaises(NotFoundError):
            self.manager.get("art_does_not_exist")

    def test_list_filters_by_run(self) -> None:
        self.manager.create(type=ArtifactType.MASK, data=b"a", creator="x", run_id="run_1")
        self.manager.create(type=ArtifactType.MASK, data=b"b", creator="x", run_id="run_2")
        self.assertEqual(len(self.manager.list(run_id="run_1")), 1)


if __name__ == "__main__":
    unittest.main()
