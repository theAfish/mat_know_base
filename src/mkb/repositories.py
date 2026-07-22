"""Public repository contracts and application-facing collection service."""

from __future__ import annotations

import json
import hashlib
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, BinaryIO, Protocol, runtime_checkable

from mkb.exceptions import ConflictError, NotFoundError, ValidationError
from mkb.models import (
    Artifact,
    Collection,
    CollectionGroup,
    Evidence,
    ExtractionSchema,
    Projection,
    Record,
    Source,
    OperationReceipt,
    StorageReference,
    WorkflowRecord,
)
from mkb.ports import ObjectStore


@runtime_checkable
class CollectionRepository(Protocol):
    def get(self, collection_id: str | uuid.UUID) -> Collection | None: ...
    def list(self, *, limit: int = 100, offset: int = 0) -> list[Collection]: ...


@runtime_checkable
class CollectionGroupRepository(Protocol):
    def create(self, group: CollectionGroup) -> CollectionGroup: ...
    def get(self, group_id: uuid.UUID) -> CollectionGroup | None: ...
    def list(self, *, limit: int = 100, offset: int = 0) -> list[CollectionGroup]: ...
    def update(self, group_id: uuid.UUID, **changes: Any) -> CollectionGroup: ...
    def delete(self, group_id: uuid.UUID) -> int: ...
    def assign(self, collection_ids: list[uuid.UUID], group_id: uuid.UUID | None) -> int: ...


@runtime_checkable
class SourceRepository(Protocol):
    def get(self, source_id: str | uuid.UUID) -> Source | None: ...
    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Source]: ...


@runtime_checkable
class ArtifactRepository(Protocol):
    def get(self, artifact_id: str | uuid.UUID) -> Artifact | None: ...
    def list(
        self,
        *,
        source_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]: ...


@runtime_checkable
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
@runtime_checkable
class ExtractionSchemaRepository(Protocol):
    def get(self, schema_id: str | uuid.UUID) -> ExtractionSchema | None: ...
    def get_by_name(self, name: str) -> ExtractionSchema | None: ...
    def get_version(
        self, schema_id: str | uuid.UUID, version: int
    ) -> ExtractionSchema | None: ...
    def list_versions(
        self,
        schema_id: str | uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExtractionSchema]: ...
    def list(self, *, limit: int = 100, offset: int = 0) -> list[ExtractionSchema]: ...
@runtime_checkable
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


@runtime_checkable
class EvidenceRepository(Protocol):
    def get(self, evidence_id: str | uuid.UUID) -> Evidence | None: ...
    def list(
        self,
        *,
        output_id: str | uuid.UUID | None = None,
        source_id: str | uuid.UUID | None = None,
        artifact_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Evidence]: ...


@runtime_checkable
class WorkflowRepository(Protocol):
    def get(self, workflow_id: str | uuid.UUID) -> WorkflowRecord | None: ...
    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowRecord]: ...


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


def _writer(repository: Any, operation: str):
    method = getattr(repository, operation, None)
    if not callable(method):
        raise ConflictError("This repository is read-only")
    return method


class Collections:
    """Typed collection operations bound to one repository instance."""

    def __init__(
        self,
        repository: CollectionRepository,
        groups: CollectionGroups | None = None,
    ):
        self._repository = repository
        self.groups = groups if groups is not None else CollectionGroups(None)

    def get(self, collection_id: str | uuid.UUID) -> Collection | None:
        return self._repository.get(_identifier(collection_id, "collection_id"))

    def require(self, collection_id: str | uuid.UUID) -> Collection:
        collection = self.get(collection_id)
        if collection is None:
            raise NotFoundError(f"Collection not found: {collection_id}")
        return collection

    def create(
        self,
        *,
        name: str,
        source_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        collection_id: str | uuid.UUID | None = None,
    ) -> Collection:
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("collection name must not be empty")
        identifier = (
            _identifier(collection_id, "collection_id")
            if collection_id is not None
            else None
        )
        return _writer(self._repository, "create")(
            name=clean_name,
            source_path=source_path,
            metadata=metadata,
            collection_id=identifier,
        )

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Collection]:
        _validate_page(limit, offset)
        return self._repository.list(limit=limit, offset=offset)

    def update(
        self,
        collection_id: str | uuid.UUID,
        *,
        name: str | None = None,
        source_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Collection:
        if name is not None and not name.strip():
            raise ValidationError("collection name must not be empty")
        return _writer(self._repository, "update")(
            _identifier(collection_id, "collection_id"),
            name=name.strip() if name is not None else None,
            source_path=source_path,
            metadata=metadata,
        )

    def delete(self, collection_id: str | uuid.UUID) -> OperationReceipt:
        identifier = _identifier(collection_id, "collection_id")
        _writer(self._repository, "delete")(identifier)
        return OperationReceipt(
            operation="collection.delete",
            status="COMPLETED",
            resource_type="collection",
            resource_id=identifier,
            created_at=datetime.now(timezone.utc),
        )

    def assign_group(
        self,
        collection_ids: list[str | uuid.UUID],
        group_id: str | uuid.UUID | None,
    ) -> OperationReceipt:
        if not collection_ids:
            raise ValidationError("collection_ids must not be empty")
        identifiers = [_identifier(item, "collection_id") for item in collection_ids]
        for identifier in identifiers:
            self.require(identifier)
        group_identifier = (
            _identifier(group_id, "group_id") if group_id is not None else None
        )
        updated = self.groups.assign(identifiers, group_identifier)
        return OperationReceipt(
            operation="collection.assign_group",
            status="COMPLETED",
            resource_type="collection_group",
            resource_id=group_identifier,
            details={"updated": updated},
            created_at=datetime.now(timezone.utc),
        )


class CollectionGroups:
    """Typed lifecycle for collection groups."""

    def __init__(self, repository: CollectionGroupRepository | None):
        self._repository = repository

    def _repo(self) -> CollectionGroupRepository:
        if self._repository is None:
            raise ConflictError("Collection grouping is unavailable for this client")
        return self._repository

    def create(
        self,
        *,
        name: str,
        description: str | None = None,
        color: str | None = None,
        display_order: int = 0,
        group_id: str | uuid.UUID | None = None,
    ) -> CollectionGroup:
        if not name.strip():
            raise ValidationError("group name must not be empty")
        now = datetime.now(timezone.utc)
        return self._repo().create(
            CollectionGroup(
                id=_identifier(group_id, "group_id") if group_id else uuid.uuid4(),
                name=name.strip(),
                description=description,
                color=color,
                display_order=display_order,
                created_at=now,
                updated_at=now,
            )
        )

    def get(self, group_id: str | uuid.UUID) -> CollectionGroup | None:
        return self._repo().get(_identifier(group_id, "group_id"))

    def require(self, group_id: str | uuid.UUID) -> CollectionGroup:
        group = self.get(group_id)
        if group is None:
            raise NotFoundError(f"Collection group not found: {group_id}")
        return group

    def list(self, *, limit: int = 100, offset: int = 0) -> list[CollectionGroup]:
        _validate_page(limit, offset)
        return self._repo().list(limit=limit, offset=offset)

    def update(
        self,
        group_id: str | uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        color: str | None = None,
        display_order: int | None = None,
    ) -> CollectionGroup:
        identifier = _identifier(group_id, "group_id")
        self.require(identifier)
        if name is not None and not name.strip():
            raise ValidationError("group name must not be empty")
        return self._repo().update(
            identifier,
            name=name.strip() if name is not None else None,
            description=description,
            color=color,
            display_order=display_order,
        )

    def delete(self, group_id: str | uuid.UUID) -> OperationReceipt:
        identifier = _identifier(group_id, "group_id")
        self.require(identifier)
        unassigned = self._repo().delete(identifier)
        return OperationReceipt(
            operation="collection_group.delete",
            status="COMPLETED",
            resource_type="collection_group",
            resource_id=identifier,
            details={"unassigned_collections": unassigned},
            created_at=datetime.now(timezone.utc),
        )

    def assign(
        self,
        collection_ids: list[uuid.UUID],
        group_id: uuid.UUID | None,
    ) -> int:
        if group_id is not None:
            self.require(group_id)
        return self._repo().assign(collection_ids, group_id)


class Sources:
    """Typed source metadata and content access for one SDK instance."""

    def __init__(
        self,
        repository: SourceRepository,
        object_store: ObjectStore,
        *,
        default_bucket: str = "raw",
        on_rollback: Callable[[Callable[[], None]], None] | None = None,
    ):
        self._repository = repository
        self._object_store = object_store
        self._default_bucket = default_bucket
        self._on_rollback = on_rollback

    def get(self, source_id: str | uuid.UUID) -> Source | None:
        return self._repository.get(_identifier(source_id, "source_id"))

    def add_bytes(
        self,
        collection_id: str | uuid.UUID,
        data: bytes,
        *,
        filename: str,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
        source_id: str | uuid.UUID | None = None,
    ) -> Source:
        if not isinstance(data, bytes):
            raise ValidationError("data must be bytes")
        clean_filename = filename.strip()
        if not clean_filename:
            raise ValidationError("filename must not be empty")
        identifier = (
            _identifier(source_id, "source_id")
            if source_id is not None
            else uuid.uuid4()
        )
        collection_identifier = _identifier(collection_id, "collection_id")
        if self._repository.get(identifier) is not None:
            raise ConflictError(f"Source already exists: {identifier}")
        creator = _writer(self._repository, "create")
        key = f"sources/{identifier}"
        if self._object_store.exists(self._default_bucket, key):
            raise ConflictError(f"Source object already exists: {key}")
        now = datetime.now(timezone.utc)
        source = Source(
            id=identifier,
            filename=clean_filename,
            media_type=media_type,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            status="STORED",
            storage={"bucket": self._default_bucket, "key": key},
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self._object_store.put_bytes(self._default_bucket, key, data)
        try:
            created = creator(source, collection_id=collection_identifier)
        except Exception:
            try:
                self._object_store.delete(self._default_bucket, key)
            except Exception:
                pass
            raise
        if self._on_rollback is not None:
            self._on_rollback(
                lambda: self._object_store.delete(self._default_bucket, key)
            )
        return created

    def add_text(
        self,
        collection_id: str | uuid.UUID,
        text: str,
        *,
        filename: str = "text.txt",
        media_type: str = "text/plain; charset=utf-8",
        metadata: dict[str, Any] | None = None,
        source_id: str | uuid.UUID | None = None,
    ) -> Source:
        if not isinstance(text, str):
            raise ValidationError("text must be a string")
        return self.add_bytes(
            collection_id,
            text.encode("utf-8"),
            filename=filename,
            media_type=media_type,
            metadata=metadata,
            source_id=source_id,
        )

    def add_file(
        self,
        collection_id: str | uuid.UUID,
        path: str | Path,
        *,
        media_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        source_id: str | uuid.UUID | None = None,
    ) -> Source:
        file_path = Path(path)
        if not file_path.is_file():
            raise ValidationError(f"source file does not exist: {file_path}")
        detected_type = media_type or mimetypes.guess_type(file_path.name)[0]
        return self.add_bytes(
            collection_id,
            file_path.read_bytes(),
            filename=file_path.name,
            media_type=detected_type or "application/octet-stream",
            metadata=metadata,
            source_id=source_id,
        )

    def add_directory(
        self,
        collection_id: str | uuid.UUID,
        path: str | Path,
        *,
        recursive: bool = True,
    ) -> list[Source]:
        directory = Path(path)
        if not directory.is_dir():
            raise ValidationError(f"source directory does not exist: {directory}")
        candidates = directory.rglob("*") if recursive else directory.glob("*")
        return [
            self.add_file(
                collection_id,
                file_path,
                metadata={"relative_path": file_path.relative_to(directory).as_posix()},
            )
            for file_path in sorted(item for item in candidates if item.is_file())
        ]

    def add_uri(
        self,
        collection_id: str | uuid.UUID,
        uri: str,
        *,
        filename: str | None = None,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
        source_id: str | uuid.UUID | None = None,
    ) -> Source:
        clean_uri = uri.strip()
        if "://" not in clean_uri:
            raise ValidationError("external source URI must include a scheme")
        identifier = (
            _identifier(source_id, "source_id")
            if source_id is not None
            else uuid.uuid4()
        )
        collection_identifier = _identifier(collection_id, "collection_id")
        if self._repository.get(identifier) is not None:
            raise ConflictError(f"Source already exists: {identifier}")
        now = datetime.now(timezone.utc)
        source = Source(
            id=identifier,
            filename=(filename or clean_uri.rstrip("/").rsplit("/", 1)[-1] or "external"),
            media_type=media_type,
            size=0,
            sha256=hashlib.sha256(clean_uri.encode()).hexdigest(),
            status="REFERENCED",
            uri=clean_uri,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        return _writer(self._repository, "create")(
            source,
            collection_id=collection_identifier,
        )

    def add_records(
        self,
        collection_id: str | uuid.UUID,
        records: list[dict[str, Any]],
        *,
        filename: str = "records.json",
        metadata: dict[str, Any] | None = None,
        source_id: str | uuid.UUID | None = None,
    ) -> Source:
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise ValidationError("records must be a list of dictionaries")
        try:
            content = json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValidationError("records must contain JSON-compatible data") from exc
        source_metadata = dict(metadata or {})
        source_metadata["record_count"] = len(records)
        source_metadata["source_type"] = "structured_records"
        return self.add_bytes(
            collection_id,
            content,
            filename=filename,
            media_type="application/json",
            metadata=source_metadata,
            source_id=source_id,
        )

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
        storage = self._managed_storage(source)
        return self._object_store.get_bytes(storage.bucket, storage.key)

    def open(self, source_id: str | uuid.UUID) -> BinaryIO:
        source = self.require(source_id)
        storage = self._managed_storage(source)
        return self._object_store.open(storage.bucket, storage.key)

    def content_exists(self, source_id: str | uuid.UUID) -> bool:
        source = self.require(source_id)
        storage = self._managed_storage(source)
        return self._object_store.exists(storage.bucket, storage.key)

    @staticmethod
    def _managed_storage(source: Source) -> StorageReference:
        if source.storage is None:
            raise ConflictError(
                f"Source {source.id} is an external reference; content is not managed"
            )
        return source.storage


class Artifacts:
    """Typed processed-artifact metadata and content access."""

    def __init__(
        self,
        repository: ArtifactRepository,
        object_store: ObjectStore,
        *,
        default_bucket: str = "processed",
        sources: Sources | None = None,
        on_rollback: Callable[[Callable[[], None]], None] | None = None,
    ):
        self._repository = repository
        self._object_store = object_store
        self._default_bucket = default_bucket
        self._sources = sources
        self._on_rollback = on_rollback

    def get(self, artifact_id: str | uuid.UUID) -> Artifact | None:
        return self._repository.get(_identifier(artifact_id, "artifact_id"))

    def add_bytes(
        self,
        source_id: str | uuid.UUID,
        data: bytes,
        *,
        processing_type: str,
        format: str,
        primary_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | uuid.UUID | None = None,
    ) -> Artifact:
        if not isinstance(data, bytes):
            raise ValidationError("data must be bytes")
        clean_processing_type = processing_type.strip()
        clean_format = format.strip()
        if not clean_processing_type or not clean_format:
            raise ValidationError("processing_type and format must not be empty")
        source_identifier = _identifier(source_id, "source_id")
        if self._sources is None:
            raise ConflictError("Artifact registration requires a source service")
        source = self._sources.require(source_identifier)
        identifier = (
            _identifier(artifact_id, "artifact_id")
            if artifact_id is not None
            else uuid.uuid4()
        )
        if self._repository.get(identifier) is not None:
            raise ConflictError(f"Artifact already exists: {identifier}")
        creator = _writer(self._repository, "create")
        key = f"artifacts/{identifier}"
        if self._object_store.exists(self._default_bucket, key):
            raise ConflictError(f"Artifact object already exists: {key}")
        now = datetime.now(timezone.utc)
        artifact = Artifact(
            id=identifier,
            source_id=source_identifier,
            processing_type=clean_processing_type,
            format=clean_format,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            source_sha256=source.sha256,
            storage={"bucket": self._default_bucket, "key": key},
            primary_path=primary_path,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self._object_store.put_bytes(self._default_bucket, key, data)
        try:
            created = creator(artifact)
        except Exception:
            try:
                self._object_store.delete(self._default_bucket, key)
            except Exception:
                pass
            raise
        if self._on_rollback is not None:
            self._on_rollback(
                lambda: self._object_store.delete(self._default_bucket, key)
            )
        return created

    def require(self, artifact_id: str | uuid.UUID) -> Artifact:
        artifact = self.get(artifact_id)
        if artifact is None:
            raise NotFoundError(f"Artifact not found: {artifact_id}")
        return artifact

    def register(
        self,
        source_id: str | uuid.UUID,
        *,
        bucket: str,
        key: str,
        processing_type: str,
        format: str,
        size: int,
        sha256: str,
        source_sha256: str | None = None,
        primary_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | uuid.UUID | None = None,
        verify_content: bool = True,
    ) -> Artifact:
        if self._sources is None:
            raise ConflictError("Artifact registration requires a source service")
        source = self._sources.require(source_id)
        identifier = (
            _identifier(artifact_id, "artifact_id")
            if artifact_id is not None
            else uuid.uuid4()
        )
        if size < 0:
            raise ValidationError("artifact size must be non-negative")
        if verify_content and not self._object_store.exists(bucket, key):
            raise NotFoundError(f"Artifact object not found: {bucket}/{key}")
        now = datetime.now(timezone.utc)
        artifact = Artifact(
            id=identifier,
            source_id=source.id,
            processing_type=processing_type,
            format=format,
            size=size,
            sha256=sha256,
            source_sha256=source_sha256 or source.sha256,
            storage=StorageReference(bucket=bucket, key=key),
            primary_path=primary_path,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        return _writer(self._repository, "create")(artifact)

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

    def __init__(
        self,
        repository: RecordRepository,
        evidence: EvidenceLinks | None = None,
    ):
        self._repository = repository
        self._evidence = evidence

    def get(self, record_id: str | uuid.UUID) -> Record | None:
        return self._repository.get(_identifier(record_id, "record_id"))

    def create(
        self,
        *,
        collection_id: str | uuid.UUID,
        data: Any,
        status: str = "COMPLETED",
        summary: str | None = None,
        source_metadata: dict[str, Any] | None = None,
        annotations: dict[str, Any] | None = None,
        record_id: str | uuid.UUID | None = None,
    ) -> Record:
        clean_status = status.strip()
        if not clean_status:
            raise ValidationError("record status must not be empty")
        identifier = (
            _identifier(record_id, "record_id") if record_id is not None else None
        )
        return _writer(self._repository, "create")(
            collection_id=_identifier(collection_id, "collection_id"),
            data=data,
            status=clean_status,
            summary=summary,
            source_metadata=source_metadata,
            annotations=annotations,
            record_id=identifier,
        )

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

    def query(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        """Query records using exact top-level data-field matches."""
        _validate_page(limit, offset)
        identifier = (
            _identifier(collection_id, "collection_id")
            if collection_id is not None
            else None
        )
        query = getattr(self._repository, "query", None)
        if callable(query):
            return query(
                collection_id=identifier,
                status=status,
                filters=filters or {},
                limit=limit,
                offset=offset,
            )
        raise ConflictError("This repository does not support record queries")

    def evidence(self, record_id: str | uuid.UUID) -> list[Evidence]:
        """Return provenance attached to one record without exposing persistence."""
        identifier = _identifier(record_id, "record_id")
        self.require(identifier)
        if self._evidence is None:
            return []
        return self._evidence.list(output_id=identifier, limit=1000)

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

    def create(
        self,
        *,
        name: str,
        domain: str,
        definition: dict[str, Any],
        system_prompt: str,
        description: str | None = None,
        purpose: str = "freeform",
        field_descriptions: dict[str, Any] | None = None,
        schema_id: str | uuid.UUID | None = None,
    ) -> ExtractionSchema:
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("schema name must not be empty")
        if not domain.strip():
            raise ValidationError("schema domain must not be empty")
        identifier = (
            _identifier(schema_id, "schema_id") if schema_id is not None else None
        )
        return _writer(self._repository, "create")(
            name=clean_name,
            domain=domain.strip(),
            definition=definition,
            system_prompt=system_prompt,
            description=description,
            purpose=purpose,
            field_descriptions=field_descriptions,
            schema_id=identifier,
        )

    def register(
        self,
        *,
        name: str,
        domain: str,
        definition: dict[str, Any],
        system_prompt: str,
        description: str | None = None,
        purpose: str = "freeform",
        field_descriptions: dict[str, Any] | None = None,
        schema_id: str | uuid.UUID | None = None,
    ) -> ExtractionSchema:
        """Register and persist a consumer schema for this client.

        This is the public registry spelling of :meth:`create`; persistence keeps
        definitions available across processes instead of hiding them in a module
        global.
        """
        return self.create(
            name=name,
            domain=domain,
            definition=definition,
            system_prompt=system_prompt,
            description=description,
            purpose=purpose,
            field_descriptions=field_descriptions,
            schema_id=schema_id,
        )

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

    def get_version(
        self, schema_id_or_name: str | uuid.UUID, version: int
    ) -> ExtractionSchema | None:
        """Return one immutable schema revision by stable identity and version."""
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValidationError("schema version must be a positive integer")
        current = self.get(schema_id_or_name)
        if current is None:
            return None
        return self._repository.get_version(current.id, version)

    def require_version(
        self, schema_id_or_name: str | uuid.UUID, version: int
    ) -> ExtractionSchema:
        schema = self.get_version(schema_id_or_name, version)
        if schema is None:
            raise NotFoundError(
                f"Extraction schema version not found: {schema_id_or_name}@{version}"
            )
        return schema

    def history(
        self,
        schema_id_or_name: str | uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExtractionSchema]:
        """List immutable revisions, newest first."""
        _validate_page(limit, offset)
        current = self.require(schema_id_or_name)
        return self._repository.list_versions(
            current.id, limit=limit, offset=offset
        )

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ExtractionSchema]:
        _validate_page(limit, offset)
        return self._repository.list(limit=limit, offset=offset)

    def update(
        self,
        schema_id_or_name: str | uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        domain: str | None = None,
        definition: dict[str, Any] | None = None,
        system_prompt: str | None = None,
        purpose: str | None = None,
        field_descriptions: dict[str, Any] | None = None,
    ) -> ExtractionSchema:
        current = self.require(schema_id_or_name)
        for field, value in (("name", name), ("domain", domain), ("purpose", purpose)):
            if value is not None and not value.strip():
                raise ValidationError(f"schema {field} must not be empty")
        return _writer(self._repository, "update")(
            current.id,
            name=name.strip() if name is not None else None,
            description=description,
            domain=domain.strip() if domain is not None else None,
            definition=definition,
            system_prompt=system_prompt,
            purpose=purpose.strip() if purpose is not None else None,
            field_descriptions=field_descriptions,
        )

    def delete(self, schema_id_or_name: str | uuid.UUID) -> OperationReceipt:
        schema = self.require(schema_id_or_name)
        _writer(self._repository, "delete")(schema.id)
        return OperationReceipt(
            operation="schema.delete",
            status="COMPLETED",
            resource_type="schema",
            resource_id=schema.id,
            created_at=datetime.now(timezone.utc),
        )


class Projections:
    """Typed projection queries that return stored JSON without normalization."""

    def __init__(self, repository: ProjectionRepository):
        self._repository = repository

    def get(self, projection_id: str | uuid.UUID) -> Projection | None:
        return self._repository.get(_identifier(projection_id, "projection_id"))

    def create(
        self,
        *,
        schema_id: str | uuid.UUID,
        record_id: str | uuid.UUID,
        data: Any,
        status: str = "COMPLETED",
        source_type: str = "record",
        validation: dict[str, Any] | None = None,
        notes: str | None = None,
        projection_id: str | uuid.UUID | None = None,
    ) -> Projection:
        clean_status = status.strip()
        if not clean_status:
            raise ValidationError("projection status must not be empty")
        clean_source_type = source_type.strip()
        if not clean_source_type:
            raise ValidationError("projection source_type must not be empty")
        identifier = (
            _identifier(projection_id, "projection_id")
            if projection_id is not None
            else None
        )
        return _writer(self._repository, "create")(
            schema_id=_identifier(schema_id, "schema_id"),
            record_id=_identifier(record_id, "record_id"),
            data=data,
            status=clean_status,
            source_type=clean_source_type,
            validation=validation,
            notes=notes,
            projection_id=identifier,
        )

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


class EvidenceLinks:
    """Typed provenance links between outputs and source material."""

    def __init__(self, repository: EvidenceRepository):
        self._repository = repository

    def create(
        self,
        *,
        output_type: str,
        output_id: str | uuid.UUID,
        source_id: str | uuid.UUID | None = None,
        artifact_id: str | uuid.UUID | None = None,
        locator: dict[str, Any] | None = None,
        excerpt: str | None = None,
        metadata: dict[str, Any] | None = None,
        evidence_id: str | uuid.UUID | None = None,
    ) -> Evidence:
        clean_output_type = output_type.strip()
        if not clean_output_type:
            raise ValidationError("evidence output_type must not be empty")
        if source_id is None and artifact_id is None:
            raise ValidationError("evidence requires a source_id or artifact_id")
        return _writer(self._repository, "create")(
            output_type=clean_output_type,
            output_id=_identifier(output_id, "output_id"),
            source_id=(
                _identifier(source_id, "source_id") if source_id is not None else None
            ),
            artifact_id=(
                _identifier(artifact_id, "artifact_id")
                if artifact_id is not None
                else None
            ),
            locator=locator,
            excerpt=excerpt,
            metadata=metadata,
            evidence_id=(
                _identifier(evidence_id, "evidence_id")
                if evidence_id is not None
                else None
            ),
        )

    def get(self, evidence_id: str | uuid.UUID) -> Evidence | None:
        return self._repository.get(_identifier(evidence_id, "evidence_id"))

    def require(self, evidence_id: str | uuid.UUID) -> Evidence:
        evidence = self.get(evidence_id)
        if evidence is None:
            raise NotFoundError(f"Evidence not found: {evidence_id}")
        return evidence

    def list(
        self,
        *,
        output_id: str | uuid.UUID | None = None,
        source_id: str | uuid.UUID | None = None,
        artifact_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Evidence]:
        _validate_page(limit, offset)
        return self._repository.list(
            output_id=(
                _identifier(output_id, "output_id") if output_id is not None else None
            ),
            source_id=(
                _identifier(source_id, "source_id") if source_id is not None else None
            ),
            artifact_id=(
                _identifier(artifact_id, "artifact_id")
                if artifact_id is not None
                else None
            ),
            limit=limit,
            offset=offset,
        )


class Workflows:
    """Read-only typed view over materials workflow extraction versions."""

    def __init__(self, repository: WorkflowRepository):
        self._repository = repository

    def get(self, workflow_id: str | uuid.UUID) -> WorkflowRecord | None:
        return self._repository.get(_identifier(workflow_id, "workflow_id"))

    def require(self, workflow_id: str | uuid.UUID) -> WorkflowRecord:
        workflow = self.get(workflow_id)
        if workflow is None:
            raise NotFoundError(f"Workflow not found: {workflow_id}")
        return workflow

    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowRecord]:
        _validate_page(limit, offset)
        return self._repository.list(
            collection_id=(
                _identifier(collection_id, "collection_id")
                if collection_id is not None
                else None
            ),
            status=status,
            limit=limit,
            offset=offset,
        )
