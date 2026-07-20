"""Portable writable repositories for new SDK-managed databases."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    func,
    insert,
    select,
)
from sqlalchemy.exc import IntegrityError

from mkb.exceptions import ConflictError, NotFoundError, ValidationError
from mkb.models import (
    Artifact,
    Collection,
    ExtractionSchema,
    Projection,
    Record,
    Source,
    StorageReference,
)
from mkb.ports import Database

generic_metadata = MetaData()

collections_table = Table(
    "mkb_collections",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False, unique=True),
    Column("source_path", String(2048)),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

sources_table = Table(
    "mkb_sources",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("filename", String(1024), nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("size", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("bucket", String(255), nullable=False),
    Column("object_key", String(2048), nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

collection_sources_table = Table(
    "mkb_collection_sources",
    generic_metadata,
    Column(
        "collection_id",
        String(36),
        ForeignKey("mkb_collections.id"),
        primary_key=True,
    ),
    Column("source_id", String(36), ForeignKey("mkb_sources.id"), primary_key=True),
)

artifacts_table = Table(
    "mkb_artifacts",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("source_id", String(36), ForeignKey("mkb_sources.id"), nullable=False),
    Column("processing_type", String(64), nullable=False),
    Column("format", String(64), nullable=False),
    Column("size", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("source_sha256", String(64), nullable=False),
    Column("bucket", String(255), nullable=False),
    Column("object_key", String(2048), nullable=False),
    Column("primary_path", String(2048)),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

records_table = Table(
    "mkb_records",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("collection_id", String(36), ForeignKey("mkb_collections.id"), nullable=False),
    Column("status", String(32), nullable=False),
    Column("data", JSON, nullable=False),
    Column("summary", String),
    Column("review_count", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("extracted_at", DateTime(timezone=True)),
    Column("source_metadata", JSON, nullable=False),
    Column("annotations", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

schemas_table = Table(
    "mkb_extraction_schemas",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False, unique=True),
    Column("description", String),
    Column("domain", String(255), nullable=False),
    Column("purpose", String(64), nullable=False),
    Column("definition", JSON, nullable=False),
    Column("system_prompt", String, nullable=False),
    Column("field_descriptions", JSON, nullable=False),
    Column("review_prompt", String),
    Column("review_trackable", Boolean, nullable=False),
    Column("review_allow_search", Boolean, nullable=False),
    Column("review_search_tools", JSON, nullable=False),
    Column("post_processors", JSON, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

projections_table = Table(
    "mkb_projections",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("schema_id", String(36), ForeignKey("mkb_extraction_schemas.id"), nullable=False),
    Column("record_id", String(36), ForeignKey("mkb_records.id"), nullable=False),
    Column("collection_id", String(36), ForeignKey("mkb_collections.id"), nullable=False),
    Column("source_type", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("data", JSON, nullable=False),
    Column("validation", JSON),
    Column("notes", String),
    Column("extracted_at", DateTime(timezone=True)),
    Column("schema_version", Integer, nullable=False),
    Column("review_count", Integer, nullable=False),
    Column("review_notes", String),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("deleted_at", DateTime(timezone=True)),
    Column("superseded_by_id", String(36)),
    Column("supersedes_ids", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

schema_migrations_table = Table(
    "mkb_schema_migrations",
    generic_metadata,
    Column("version", Integer, primary_key=True),
    Column("name", String(255), nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)

CURRENT_SCHEMA_VERSION = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: Any, field: str) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must contain JSON-compatible data") from exc
    return value


class GenericSchemaManager:
    """Explicit, additive initializer for SDK-owned portable tables."""

    def __init__(self, database: Database):
        self._database = database

    def initialize(self) -> int:
        with self._database.transaction() as session:
            connection = session.connection()
            schema_migrations_table.create(connection, checkfirst=True)
            current = session.execute(
                select(func.max(schema_migrations_table.c.version))
            ).scalar_one()
            if current is not None and current > CURRENT_SCHEMA_VERSION:
                raise ConflictError(
                    f"Database schema version {current} is newer than this SDK supports"
                )
            generic_metadata.create_all(connection, checkfirst=True)
            if current is None:
                session.execute(
                    insert(schema_migrations_table).values(
                        version=1,
                        name="collections_sources_records_schemas",
                        applied_at=_now(),
                    )
                )
                current = 1
            if current < 2:
                session.execute(
                    insert(schema_migrations_table).values(
                        version=2,
                        name="artifacts_and_projections",
                        applied_at=_now(),
                    )
                )
                current = 2
            return int(current)

    def version(self) -> int | None:
        with self._database.session() as session:
            try:
                value = session.execute(
                    select(func.max(schema_migrations_table.c.version))
                ).scalar_one()
            except Exception:
                return None
            return int(value) if value is not None else None


class _Repository:
    def __init__(self, database: Database, session=None):
        self._database = database
        self._bound_session = session

    @contextmanager
    def _session(self, *, write: bool = False) -> Iterator[Any]:
        if self._bound_session is not None:
            yield self._bound_session
        elif write:
            with self._database.transaction() as session:
                yield session
        else:
            with self._database.session() as session:
                yield session


class GenericCollectionRepository(_Repository):
    """Portable collection CRUD foundation for new databases."""

    @staticmethod
    def _model(row) -> Collection:
        values = row._mapping
        return Collection(
            id=uuid.UUID(values["id"]),
            name=values["name"],
            source_path=values["source_path"],
            source_count=int(values.get("source_count", 0)),
            metadata=dict(values["metadata"] or {}),
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )

    def create(
        self,
        *,
        name: str,
        source_path: str | None = None,
        metadata: dict[str, Any] | None = None,
        collection_id: uuid.UUID | None = None,
    ) -> Collection:
        identifier = collection_id or uuid.uuid4()
        now = _now()
        values = {
            "id": str(identifier),
            "name": name,
            "source_path": source_path,
            "metadata": _json_value(dict(metadata or {}), "metadata"),
            "created_at": now,
            "updated_at": now,
        }
        try:
            with self._session(write=True) as session:
                session.execute(insert(collections_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Collection already exists: {name}") from exc
        return Collection(id=identifier, source_count=0, **{k: v for k, v in values.items() if k != "id"})

    def get(self, collection_id: str | uuid.UUID) -> Collection | None:
        source_count = (
            select(func.count())
            .select_from(collection_sources_table)
            .where(collection_sources_table.c.collection_id == collections_table.c.id)
            .correlate(collections_table)
            .scalar_subquery()
            .label("source_count")
        )
        statement = select(collections_table, source_count).where(
            collections_table.c.id == str(collection_id)
        )
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Collection]:
        source_count = (
            select(func.count())
            .select_from(collection_sources_table)
            .where(collection_sources_table.c.collection_id == collections_table.c.id)
            .correlate(collections_table)
            .scalar_subquery()
            .label("source_count")
        )
        statement = (
            select(collections_table, source_count)
            .order_by(collections_table.c.created_at.desc(), collections_table.c.id)
            .limit(limit)
            .offset(offset)
        )
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]


class GenericSourceRepository(_Repository):
    """Portable metadata and collection membership for object-backed sources."""

    @staticmethod
    def _model(row, collection_ids: tuple[uuid.UUID, ...]) -> Source:
        values = row._mapping
        return Source(
            id=uuid.UUID(values["id"]),
            filename=values["filename"],
            media_type=values["media_type"],
            size=values["size"],
            sha256=values["sha256"],
            status=values["status"],
            storage=StorageReference(
                bucket=values["bucket"], key=values["object_key"]
            ),
            collection_ids=collection_ids,
            metadata=dict(values["metadata"] or {}),
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )

    def _collections(self, session, source_id: str) -> tuple[uuid.UUID, ...]:
        statement = select(collection_sources_table.c.collection_id).where(
            collection_sources_table.c.source_id == source_id
        )
        return tuple(uuid.UUID(value) for value in session.scalars(statement))

    def create(self, source: Source, *, collection_id: uuid.UUID) -> Source:
        now = source.created_at or _now()
        values = {
            "id": str(source.id),
            "filename": source.filename,
            "media_type": source.media_type,
            "size": source.size,
            "sha256": source.sha256,
            "status": source.status,
            "bucket": source.storage.bucket,
            "object_key": source.storage.key,
            "metadata": _json_value(dict(source.metadata), "metadata"),
            "created_at": now,
            "updated_at": source.updated_at or now,
        }
        try:
            with self._session(write=True) as session:
                exists = session.execute(
                    select(collections_table.c.id).where(
                        collections_table.c.id == str(collection_id)
                    )
                ).scalar_one_or_none()
                if exists is None:
                    raise NotFoundError(f"Collection not found: {collection_id}")
                session.execute(insert(sources_table).values(**values))
                session.execute(
                    insert(collection_sources_table).values(
                        collection_id=str(collection_id), source_id=str(source.id)
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(f"Source already exists: {source.id}") from exc
        return source.model_copy(update={"collection_ids": (collection_id,)})

    def get(self, source_id: str | uuid.UUID) -> Source | None:
        identifier = str(source_id)
        statement = select(sources_table).where(sources_table.c.id == identifier)
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row, self._collections(session, identifier)) if row else None

    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Source]:
        statement = select(sources_table)
        if collection_id is not None:
            statement = statement.join(
                collection_sources_table,
                collection_sources_table.c.source_id == sources_table.c.id,
            ).where(collection_sources_table.c.collection_id == str(collection_id))
        statement = statement.order_by(
            sources_table.c.created_at.desc(), sources_table.c.id
        ).limit(limit).offset(offset)
        with self._session() as session:
            rows = list(session.execute(statement))
            return [
                self._model(row, self._collections(session, row._mapping["id"]))
                for row in rows
            ]


class GenericArtifactRepository(_Repository):
    """Portable metadata for object-backed derived artifacts."""

    @staticmethod
    def _model(row) -> Artifact:
        values = row._mapping
        return Artifact(
            id=uuid.UUID(values["id"]),
            source_id=uuid.UUID(values["source_id"]),
            processing_type=values["processing_type"],
            format=values["format"],
            size=values["size"],
            sha256=values["sha256"],
            source_sha256=values["source_sha256"],
            storage=StorageReference(
                bucket=values["bucket"], key=values["object_key"]
            ),
            primary_path=values["primary_path"],
            metadata=dict(values["metadata"] or {}),
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )

    def create(self, artifact: Artifact) -> Artifact:
        now = artifact.created_at or _now()
        values = {
            "id": str(artifact.id),
            "source_id": str(artifact.source_id),
            "processing_type": artifact.processing_type,
            "format": artifact.format,
            "size": artifact.size,
            "sha256": artifact.sha256,
            "source_sha256": artifact.source_sha256,
            "bucket": artifact.storage.bucket,
            "object_key": artifact.storage.key,
            "primary_path": artifact.primary_path,
            "metadata": _json_value(dict(artifact.metadata), "metadata"),
            "created_at": now,
            "updated_at": artifact.updated_at or now,
        }
        try:
            with self._session(write=True) as session:
                exists = session.execute(
                    select(sources_table.c.id).where(
                        sources_table.c.id == str(artifact.source_id)
                    )
                ).scalar_one_or_none()
                if exists is None:
                    raise NotFoundError(f"Source not found: {artifact.source_id}")
                session.execute(insert(artifacts_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Artifact already exists: {artifact.id}") from exc
        return artifact

    def get(self, artifact_id: str | uuid.UUID) -> Artifact | None:
        statement = select(artifacts_table).where(
            artifacts_table.c.id == str(artifact_id)
        )
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

    def list(
        self,
        *,
        source_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        statement = select(artifacts_table)
        if source_id is not None:
            statement = statement.where(
                artifacts_table.c.source_id == str(source_id)
            )
        statement = statement.order_by(
            artifacts_table.c.created_at.desc(), artifacts_table.c.id
        ).limit(limit).offset(offset)
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]


class GenericRecordRepository(_Repository):
    """Portable structured-record repository for consumer-owned JSON."""

    @staticmethod
    def _model(row) -> Record:
        values = row._mapping
        return Record(
            id=uuid.UUID(values["id"]),
            collection_id=uuid.UUID(values["collection_id"]),
            status=values["status"],
            data=values["data"],
            summary=values["summary"],
            review_count=int(values["review_count"]),
            version=int(values["version"]),
            extracted_at=values["extracted_at"],
            source_metadata=dict(values["source_metadata"] or {}),
            annotations=dict(values["annotations"] or {}),
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )

    def create(
        self,
        *,
        collection_id: uuid.UUID,
        data: Any,
        status: str = "COMPLETED",
        summary: str | None = None,
        source_metadata: dict[str, Any] | None = None,
        annotations: dict[str, Any] | None = None,
        record_id: uuid.UUID | None = None,
    ) -> Record:
        identifier = record_id or uuid.uuid4()
        now = _now()
        values = {
            "id": str(identifier),
            "collection_id": str(collection_id),
            "status": status,
            "data": _json_value(data, "data"),
            "summary": summary,
            "review_count": 0,
            "version": 1,
            "extracted_at": now if status == "COMPLETED" else None,
            "source_metadata": _json_value(
                dict(source_metadata or {}), "source_metadata"
            ),
            "annotations": _json_value(dict(annotations or {}), "annotations"),
            "created_at": now,
            "updated_at": now,
        }
        try:
            with self._session(write=True) as session:
                exists = session.execute(
                    select(collections_table.c.id).where(
                        collections_table.c.id == str(collection_id)
                    )
                ).scalar_one_or_none()
                if exists is None:
                    raise NotFoundError(f"Collection not found: {collection_id}")
                session.execute(insert(records_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Record already exists: {identifier}") from exc
        return Record(
            id=identifier,
            collection_id=collection_id,
            status=status,
            data=data,
            summary=summary,
            version=1,
            extracted_at=values["extracted_at"],
            source_metadata=values["source_metadata"],
            annotations=values["annotations"],
            created_at=now,
            updated_at=now,
        )

    def get(self, record_id: str | uuid.UUID) -> Record | None:
        statement = select(records_table).where(records_table.c.id == str(record_id))
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

    def get_for_collection(self, collection_id: str | uuid.UUID) -> Record | None:
        statement = (
            select(records_table)
            .where(records_table.c.collection_id == str(collection_id))
            .order_by(records_table.c.created_at.desc(), records_table.c.id)
            .limit(1)
        )
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        statement = select(records_table)
        if collection_id is not None:
            statement = statement.where(records_table.c.collection_id == str(collection_id))
        if status is not None:
            statement = statement.where(records_table.c.status == status)
        statement = statement.order_by(
            records_table.c.created_at.desc(), records_table.c.id
        ).limit(limit).offset(offset)
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]


class GenericExtractionSchemaRepository(_Repository):
    """Portable schema registry for custom extraction definitions."""

    @staticmethod
    def _model(row) -> ExtractionSchema:
        values = row._mapping
        return ExtractionSchema(
            id=uuid.UUID(values["id"]),
            name=values["name"],
            description=values["description"],
            domain=values["domain"],
            purpose=values["purpose"],
            definition=dict(values["definition"] or {}),
            system_prompt=values["system_prompt"],
            field_descriptions=dict(values["field_descriptions"] or {}),
            review_prompt=values["review_prompt"],
            review_trackable=bool(values["review_trackable"]),
            review_allow_search=bool(values["review_allow_search"]),
            review_search_tools=tuple(values["review_search_tools"] or ()),
            post_processors=tuple(values["post_processors"] or ()),
            version=int(values["version"]),
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )

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
        schema_id: uuid.UUID | None = None,
    ) -> ExtractionSchema:
        identifier = schema_id or uuid.uuid4()
        now = _now()
        values = {
            "id": str(identifier),
            "name": name,
            "description": description,
            "domain": domain,
            "purpose": purpose,
            "definition": _json_value(dict(definition), "definition"),
            "system_prompt": system_prompt,
            "field_descriptions": _json_value(
                dict(field_descriptions or {}), "field_descriptions"
            ),
            "review_prompt": None,
            "review_trackable": True,
            "review_allow_search": False,
            "review_search_tools": [],
            "post_processors": [],
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }
        try:
            with self._session(write=True) as session:
                session.execute(insert(schemas_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Extraction schema already exists: {name}") from exc
        return ExtractionSchema(
            id=identifier,
            name=name,
            description=description,
            domain=domain,
            purpose=purpose,
            definition=definition,
            system_prompt=system_prompt,
            field_descriptions=values["field_descriptions"],
            created_at=now,
            updated_at=now,
        )

    def get(self, schema_id: str | uuid.UUID) -> ExtractionSchema | None:
        statement = select(schemas_table).where(schemas_table.c.id == str(schema_id))
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

    def get_by_name(self, name: str) -> ExtractionSchema | None:
        statement = select(schemas_table).where(schemas_table.c.name == name)
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ExtractionSchema]:
        statement = select(schemas_table).order_by(schemas_table.c.name).limit(limit).offset(offset)
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]


class GenericProjectionRepository(_Repository):
    """Portable writable projections for consumer-defined schemas."""

    @staticmethod
    def _model(row) -> Projection:
        values = row._mapping
        return Projection(
            id=uuid.UUID(values["id"]),
            schema_id=uuid.UUID(values["schema_id"]),
            record_id=uuid.UUID(values["record_id"]),
            collection_id=uuid.UUID(values["collection_id"]),
            source_type=values["source_type"],
            status=values["status"],
            data=values["data"],
            validation=values["validation"],
            notes=values["notes"],
            extracted_at=values["extracted_at"],
            schema_version=values["schema_version"],
            review_count=values["review_count"],
            review_notes=values["review_notes"],
            reviewed_at=values["reviewed_at"],
            deleted_at=values["deleted_at"],
            superseded_by_id=(
                uuid.UUID(values["superseded_by_id"])
                if values["superseded_by_id"]
                else None
            ),
            supersedes_ids=tuple(values["supersedes_ids"] or ()),
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )

    def create(
        self,
        *,
        schema_id: uuid.UUID,
        record_id: uuid.UUID,
        data: Any,
        status: str = "COMPLETED",
        source_type: str = "record",
        validation: dict[str, Any] | None = None,
        notes: str | None = None,
        projection_id: uuid.UUID | None = None,
    ) -> Projection:
        identifier = projection_id or uuid.uuid4()
        now = _now()
        try:
            with self._session(write=True) as session:
                record = session.execute(
                    select(records_table.c.collection_id).where(
                        records_table.c.id == str(record_id)
                    )
                ).one_or_none()
                if record is None:
                    raise NotFoundError(f"Record not found: {record_id}")
                schema_version = session.execute(
                    select(schemas_table.c.version).where(
                        schemas_table.c.id == str(schema_id)
                    )
                ).scalar_one_or_none()
                if schema_version is None:
                    raise NotFoundError(f"Extraction schema not found: {schema_id}")
                values = {
                    "id": str(identifier),
                    "schema_id": str(schema_id),
                    "record_id": str(record_id),
                    "collection_id": record.collection_id,
                    "source_type": source_type,
                    "status": status,
                    "data": _json_value(data, "data"),
                    "validation": (
                        _json_value(dict(validation), "validation")
                        if validation is not None
                        else None
                    ),
                    "notes": notes,
                    "extracted_at": now if status == "COMPLETED" else None,
                    "schema_version": schema_version,
                    "review_count": 0,
                    "review_notes": None,
                    "reviewed_at": None,
                    "deleted_at": None,
                    "superseded_by_id": None,
                    "supersedes_ids": [],
                    "created_at": now,
                    "updated_at": now,
                }
                session.execute(insert(projections_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Projection already exists: {identifier}") from exc
        return Projection(
            id=identifier,
            schema_id=schema_id,
            record_id=record_id,
            collection_id=uuid.UUID(values["collection_id"]),
            source_type=source_type,
            status=status,
            data=data,
            validation=validation,
            notes=notes,
            extracted_at=values["extracted_at"],
            schema_version=schema_version,
            created_at=now,
            updated_at=now,
        )

    def get(self, projection_id: str | uuid.UUID) -> Projection | None:
        statement = select(projections_table).where(
            projections_table.c.id == str(projection_id)
        )
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

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
        statement = select(projections_table)
        filters = {
            projections_table.c.schema_id: schema_id,
            projections_table.c.record_id: record_id,
            projections_table.c.collection_id: collection_id,
            projections_table.c.status: status,
        }
        for column, value in filters.items():
            if value is not None:
                statement = statement.where(column == str(value))
        if not include_deleted:
            statement = statement.where(projections_table.c.deleted_at.is_(None))
        if not include_history:
            statement = statement.where(projections_table.c.superseded_by_id.is_(None))
        statement = statement.order_by(
            projections_table.c.created_at.desc(), projections_table.c.id
        )
        with self._session() as session:
            models = [self._model(row) for row in session.execute(statement)]
        if newest_only:
            seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
            newest = []
            for model in models:
                key = (model.schema_id, model.record_id)
                if key not in seen:
                    newest.append(model)
                    seen.add(key)
            models = newest
        return models[offset : offset + limit]
