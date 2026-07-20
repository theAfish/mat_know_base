"""Public repository contracts and application-facing collection service."""

from __future__ import annotations

import json
import uuid
from typing import Any, BinaryIO, Protocol

from mkb.exceptions import NotFoundError, ValidationError
from mkb.models import Artifact, Collection, ExtractionSchema, Projection, Record, Source
from mkb.ports import ObjectStore


class CollectionRepository(Protocol):
    def get(self, collection_id: str | uuid.UUID) -> Collection | None: ...
    def list(self, *, limit: int = 100, offset: int = 0) -> list[Collection]: ...


class SourceRepository(Protocol):
    def get(self, source_id: str | uuid.UUID) -> Source | None: ...
    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Source]: ...


class ArtifactRepository(Protocol):
    def get(self, artifact_id: str | uuid.UUID) -> Artifact | None: ...
    def list(
        self,
        *,
        source_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]: ...


class RecordRepository(Protocol):
    def get(self, record_id: str | uuid.UUID) -> Record | None: ...
    def get_for_collection(self, collection_id: str | uuid.UUID) -> Record | None: ...
    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]: ...


class ExtractionSchemaRepository(Protocol):
    def get(self, schema_id: str | uuid.UUID) -> ExtractionSchema | None: ...
    def get_by_name(self, name: str) -> ExtractionSchema | None: ...
    def list(self, *, limit: int = 100, offset: int = 0) -> list[ExtractionSchema]: ...


class ProjectionRepository(Protocol):
    def get(self, projection_id: str | uuid.UUID) -> Projection | None: ...
    def list(
        self,
        *,
        schema_id: str | uuid.UUID | None = None,
        record_id: str | uuid.UUID | None = None,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        include_deleted: bool = False,
        include_history: bool = False,
        newest_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Projection]: ...


def _validate_page(limit: int, offset: int) -> None:
    if limit < 1 or limit > 1000:
        raise ValidationError("limit must be between 1 and 1000")
    if offset < 0:
        raise ValidationError("offset must be non-negative")


def _identifier(value: str | uuid.UUID, field: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError(f"{field} must be a UUID") from exc


def _export_json(rows: list[Any], indent: int | None) -> str:
    payload = [row.model_dump(mode="json") for row in rows]
    return json.dumps(payload, ensure_ascii=False, indent=indent)


class Collections:
    """Typed collection operations bound to one repository instance."""

    def __init__(self, repository: CollectionRepository):
        self._repository = repository

    def get(self, collection_id: str | uuid.UUID) -> Collection | None:
        return self._repository.get(_identifier(collection_id, "collection_id"))

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Collection]:
        _validate_page(limit, offset)
        return self._repository.list(limit=limit, offset=offset)


class Sources:
    """Typed source metadata and content access for one SDK instance."""

    def __init__(self, repository: SourceRepository, object_store: ObjectStore):
        self._repository = repository
        self._object_store = object_store

    def get(self, source_id: str | uuid.UUID) -> Source | None:
        return self._repository.get(_identifier(source_id, "source_id"))

    def require(self, source_id: str | uuid.UUID) -> Source:
        source = self.get(source_id)
        if source is None:
            raise NotFoundError(f"Source not found: {source_id}")
        return source

    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Source]:
        _validate_page(limit, offset)
        identifier = (
            _identifier(collection_id, "collection_id")
            if collection_id is not None
            else None
        )
        return self._repository.list(collection_id=identifier, limit=limit, offset=offset)

    def read_bytes(self, source_id: str | uuid.UUID) -> bytes:
        source = self.require(source_id)
        return self._object_store.get_bytes(source.storage.bucket, source.storage.key)

    def open(self, source_id: str | uuid.UUID) -> BinaryIO:
        source = self.require(source_id)
        return self._object_store.open(source.storage.bucket, source.storage.key)

    def content_exists(self, source_id: str | uuid.UUID) -> bool:
        source = self.require(source_id)
        return self._object_store.exists(source.storage.bucket, source.storage.key)


class Artifacts:
    """Typed processed-artifact metadata and content access."""

    def __init__(self, repository: ArtifactRepository, object_store: ObjectStore):
        self._repository = repository
        self._object_store = object_store

    def get(self, artifact_id: str | uuid.UUID) -> Artifact | None:
        return self._repository.get(_identifier(artifact_id, "artifact_id"))

    def require(self, artifact_id: str | uuid.UUID) -> Artifact:
        artifact = self.get(artifact_id)
        if artifact is None:
            raise NotFoundError(f"Artifact not found: {artifact_id}")
        return artifact

    def list(
        self,
        *,
        source_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        _validate_page(limit, offset)
        identifier = _identifier(source_id, "source_id") if source_id is not None else None
        return self._repository.list(source_id=identifier, limit=limit, offset=offset)

    def read_bytes(self, artifact_id: str | uuid.UUID) -> bytes:
        artifact = self.require(artifact_id)
        return self._object_store.get_bytes(artifact.storage.bucket, artifact.storage.key)

    def open(self, artifact_id: str | uuid.UUID) -> BinaryIO:
        artifact = self.require(artifact_id)
        return self._object_store.open(artifact.storage.bucket, artifact.storage.key)

    def content_exists(self, artifact_id: str | uuid.UUID) -> bool:
        artifact = self.require(artifact_id)
        return self._object_store.exists(artifact.storage.bucket, artifact.storage.key)


class Records:
    """Typed access to extracted records without changing legacy frame data."""

    def __init__(self, repository: RecordRepository):
        self._repository = repository

    def get(self, record_id: str | uuid.UUID) -> Record | None:
        return self._repository.get(_identifier(record_id, "record_id"))

    def require(self, record_id: str | uuid.UUID) -> Record:
        record = self.get(record_id)
        if record is None:
            raise NotFoundError(f"Record not found: {record_id}")
        return record

    def get_for_collection(self, collection_id: str | uuid.UUID) -> Record | None:
        identifier = _identifier(collection_id, "collection_id")
        return self._repository.get_for_collection(identifier)

    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        _validate_page(limit, offset)
        identifier = (
            _identifier(collection_id, "collection_id")
            if collection_id is not None
            else None
        )
        return self._repository.list(
            collection_id=identifier,
            status=status,
            limit=limit,
            offset=offset,
        )

    def export_json(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
        indent: int | None = 2,
    ) -> str:
        """Serialize a bounded record query without writing to the filesystem."""
        return _export_json(
            self.list(
                collection_id=collection_id,
                status=status,
                limit=limit,
                offset=offset,
            ),
            indent,
        )


class ExtractionSchemas:
    """Typed access to versioned extraction-space definitions."""

    def __init__(self, repository: ExtractionSchemaRepository):
        self._repository = repository

    def get(self, schema_id_or_name: str | uuid.UUID) -> ExtractionSchema | None:
        if isinstance(schema_id_or_name, uuid.UUID):
            return self._repository.get(schema_id_or_name)
        try:
            identifier = uuid.UUID(str(schema_id_or_name))
        except (TypeError, ValueError, AttributeError):
            name = str(schema_id_or_name).strip()
            if not name:
                raise ValidationError("schema name must not be empty")
            return self._repository.get_by_name(name)
        return self._repository.get(identifier)

    def require(self, schema_id_or_name: str | uuid.UUID) -> ExtractionSchema:
        schema = self.get(schema_id_or_name)
        if schema is None:
            raise NotFoundError(f"Extraction schema not found: {schema_id_or_name}")
        return schema

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ExtractionSchema]:
        _validate_page(limit, offset)
        return self._repository.list(limit=limit, offset=offset)


class Projections:
    """Typed projection queries that return stored JSON without normalization."""

    def __init__(self, repository: ProjectionRepository):
        self._repository = repository

    def get(self, projection_id: str | uuid.UUID) -> Projection | None:
        return self._repository.get(_identifier(projection_id, "projection_id"))

    def require(self, projection_id: str | uuid.UUID) -> Projection:
        projection = self.get(projection_id)
        if projection is None:
            raise NotFoundError(f"Projection not found: {projection_id}")
        return projection

    def list(
        self,
        *,
        schema_id: str | uuid.UUID | None = None,
        record_id: str | uuid.UUID | None = None,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        include_deleted: bool = False,
        include_history: bool = False,
        newest_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Projection]:
        _validate_page(limit, offset)
        identifiers = {
            "schema_id": (
                _identifier(schema_id, "schema_id") if schema_id is not None else None
            ),
            "record_id": (
                _identifier(record_id, "record_id") if record_id is not None else None
            ),
            "collection_id": (
                _identifier(collection_id, "collection_id")
                if collection_id is not None
                else None
            ),
        }
        return self._repository.list(
            **identifiers,
            status=status,
            include_deleted=include_deleted,
            include_history=include_history,
            newest_only=newest_only,
            limit=limit,
            offset=offset,
        )

    def export_json(
        self,
        *,
        schema_id: str | uuid.UUID | None = None,
        record_id: str | uuid.UUID | None = None,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        include_deleted: bool = False,
        include_history: bool = False,
        newest_only: bool = False,
        limit: int = 1000,
        offset: int = 0,
        indent: int | None = 2,
    ) -> str:
        """Serialize a bounded projection query without altering stored payloads."""
        rows = self.list(
            schema_id=schema_id,
            record_id=record_id,
            collection_id=collection_id,
            status=status,
            include_deleted=include_deleted,
            include_history=include_history,
            newest_only=newest_only,
            limit=limit,
            offset=offset,
        )
        return _export_json(rows, indent)
