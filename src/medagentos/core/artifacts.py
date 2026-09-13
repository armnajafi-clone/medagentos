"""Artifact Manager.

Owns artifact identity, versioning, provenance and location. It does not store
bytes: payloads go to an ``ObjectStore`` port, metadata to an
``ArtifactRepository`` port. That split is DATABASE_DESIGN.md's rule that the
database holds references, not medical files.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .entities import Artifact, ArtifactType, JsonDict, TraceEventType
from .errors import NotFoundError, StorageError
from .ids import digest_bytes
from .ports import ArtifactRepository, ObjectStore
from .trace import TraceEngine

_EXTENSIONS = {
    ArtifactType.IMAGE: "bin",
    ArtifactType.MASK: "bin",
    ArtifactType.REPORT: "md",
    ArtifactType.EMBEDDING: "json",
    ArtifactType.EVALUATION: "json",
    ArtifactType.DOCUMENT: "json",
}


class ArtifactManager:
    """Creates, versions and resolves artifacts."""

    def __init__(
        self,
        repository: ArtifactRepository,
        object_store: ObjectStore,
        *,
        trace: TraceEngine | None = None,
    ) -> None:
        self._repository = repository
        self._objects = object_store
        self._trace = trace

    def create(
        self,
        *,
        type: ArtifactType,
        data: bytes,
        creator: str,
        case_id: str | None = None,
        run_id: str | None = None,
        parent_ids: tuple[str, ...] = (),
        metadata: JsonDict | None = None,
        content_type: str = "application/octet-stream",
    ) -> Artifact:
        """Store a payload and register its metadata.

        The storage key embeds the content digest, so identical payloads land on
        the same key and a re-run that produces the same bytes costs no extra
        storage — one half of the reproducibility guarantee in D-09.
        """
        digest = digest_bytes(data)
        key = self._key_for(type, digest)
        try:
            location = self._objects.put(key, data, content_type=content_type)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"could not store artifact payload at {key}: {exc}") from exc

        artifact = Artifact(
            type=type,
            creator=creator,
            location=location,
            digest=digest,
            case_id=case_id,
            run_id=run_id,
            parent_ids=parent_ids,
            metadata=dict(metadata or {}),
        )
        saved = self._repository.save_artifact(artifact)
        if self._trace is not None:
            self._trace.record(
                TraceEventType.ARTIFACT_CREATED,
                creator,
                output_digest=digest,
                artifact_id=saved.id,
                artifact_type=type.value,
                location=location,
                size_bytes=len(data),
            )
        return saved

    def create_json(self, *, payload: Any, **kwargs: Any) -> Artifact:
        """Store a JSON-serialisable payload. Keys are sorted so the digest is stable."""
        kwargs.setdefault("type", ArtifactType.DOCUMENT)
        data = json.dumps(payload, sort_keys=True, indent=2, default=str).encode("utf-8")
        return self.create(data=data, content_type="application/json", **kwargs)

    def create_text(self, *, text: str, content_type: str = "text/markdown", **kwargs: Any) -> Artifact:
        kwargs.setdefault("type", ArtifactType.REPORT)
        return self.create(data=text.encode("utf-8"), content_type=content_type, **kwargs)

    def get(self, artifact_id: str) -> Artifact:
        artifact = self._repository.get_artifact(artifact_id)
        if artifact is None:
            raise NotFoundError(f"artifact {artifact_id!r} does not exist")
        return artifact

    def read(self, artifact_id: str) -> bytes:
        artifact = self.get(artifact_id)
        key = artifact.location.rsplit("/", 1)[-1]
        try:
            return self._objects.get(self._key_from_location(artifact.location) or key)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"could not read artifact {artifact_id}: {exc}") from exc

    def read_json(self, artifact_id: str) -> Any:
        return json.loads(self.read(artifact_id).decode("utf-8"))

    def new_version(
        self, artifact_id: str, *, data: bytes, creator: str, metadata: JsonDict | None = None
    ) -> Artifact:
        """Supersede an artifact, keeping the old one and recording the lineage."""
        previous = self.get(artifact_id)
        digest = digest_bytes(data)
        key = self._key_for(previous.type, digest)
        location = self._objects.put(key, data)
        successor = previous.next_version(location=location, digest=digest, creator=creator)
        if metadata:
            successor = replace(successor, metadata={**previous.metadata, **metadata})
        saved = self._repository.save_artifact(successor)
        if self._trace is not None:
            self._trace.record(
                TraceEventType.ARTIFACT_CREATED,
                creator,
                output_digest=digest,
                artifact_id=saved.id,
                supersedes=previous.id,
                version=saved.version,
            )
        return saved

    def lineage(self, artifact_id: str) -> list[Artifact]:
        """Return the provenance chain, oldest ancestor first.

        SAFETY_MODEL.md asks "where did this output come from?"; this is the
        machine-readable answer for any artifact.
        """
        artifact = self.get(artifact_id)
        chain: list[Artifact] = []
        seen: set[str] = set()
        frontier = list(artifact.parent_ids)
        while frontier:
            parent_id = frontier.pop(0)
            if parent_id in seen:
                continue
            seen.add(parent_id)
            parent = self._repository.get_artifact(parent_id)
            if parent is None:
                continue
            chain.append(parent)
            frontier.extend(parent.parent_ids)
        chain.sort(key=lambda a: a.created_at)
        chain.append(artifact)
        return chain

    def list(self, *, case_id: str | None = None, run_id: str | None = None) -> list[Artifact]:
        return self._repository.list_artifacts(case_id=case_id, run_id=run_id)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _key_for(type: ArtifactType, digest: str) -> str:
        # Shard by the first two digest characters: object stores and filesystems
        # both degrade with very wide flat directories.
        raw = digest.split(":", 1)[-1]
        return f"artifacts/{type.value}/{raw[:2]}/{raw}.{_EXTENSIONS.get(type, 'bin')}"

    @staticmethod
    def _key_from_location(location: str) -> str | None:
        """Recover the store key from a location URI produced by any ObjectStore."""
        marker = "artifacts/"
        index = location.find(marker)
        return location[index:] if index != -1 else None
