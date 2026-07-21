"""Portable writable repositories for new SDK-managed databases."""

from __future__ import annotations

import json
import time
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
    delete,
    insert,
    inspect as sa_inspect,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from mkb.exceptions import ConflictError, NotFoundError, ValidationError
from mkb.models import (
    Artifact,
    Collection,
    CollectionGroup,
    Evidence,
    ExtractionSchema,
    FeedbackItem,
    Projection,
    PostProcessor,
    Record,
    Skill,
    Source,
    StorageReference,
    Job,
)
from mkb.ports import Capabilities, Database

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

collection_groups_table = Table(
    "mkb_collection_groups",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("description", String),
    Column("color", String(32)),
    Column("display_order", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

collection_group_memberships_table = Table(
    "mkb_collection_group_memberships",
    generic_metadata,
    Column(
        "collection_id",
        String(36),
        ForeignKey("mkb_collections.id"),
        primary_key=True,
    ),
    Column("group_id", String(36), ForeignKey("mkb_collection_groups.id"), nullable=False),
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
    Column("uri", String),
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

schema_versions_table = Table(
    "mkb_extraction_schema_versions",
    generic_metadata,
    Column(
        "schema_id",
        String(36),
        ForeignKey("mkb_extraction_schemas.id"),
        primary_key=True,
    ),
    Column("version", Integer, primary_key=True),
    Column("name", String(255), nullable=False),
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

evidence_table = Table(
    "mkb_evidence",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("output_type", String(64), nullable=False),
    Column("output_id", String(36), nullable=False),
    Column("source_id", String(36)),
    Column("artifact_id", String(36)),
    Column("locator", JSON, nullable=False),
    Column("excerpt", String),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

jobs_table = Table(
    "mkb_jobs",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("kind", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("label", String(255)),
    Column("pipeline_name", String(255)),
    Column("pipeline_version", String(64)),
    Column("run_id", String(36)),
    Column("idempotency_key", String(255), unique=True),
    Column("inputs", JSON, nullable=False),
    Column("parameters", JSON, nullable=False),
    Column("checkpoint", JSON, nullable=False),
    Column("events", JSON, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("progress", String(32)),
    Column("message", String),
    Column("result", JSON),
    Column("error", String),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
)

feedback_table = Table(
    "mkb_feedback",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("target_record_id", String(36), nullable=False),
    Column("target_collection_id", String(36), nullable=False),
    Column("category", String(64), nullable=False),
    Column("question", String, nullable=False),
    Column("source_agent", String(100), nullable=False),
    Column("source_projection_id", String(36)),
    Column("field_path", String),
    Column("context", String),
    Column("status", String(32), nullable=False),
    Column("resolution_notes", String),
    Column("resolved_by", String(100)),
    Column("resolved_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

skills_table = Table(
    "mkb_skills",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("slug", String(255), nullable=False, unique=True),
    Column("content", String, nullable=False),
    Column("description", String),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

post_processors_table = Table(
    "mkb_post_processors",
    generic_metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("filename", String(255), nullable=False),
    Column("source", String, nullable=False),
    Column("metadata", JSON, nullable=False),
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

CURRENT_SCHEMA_VERSION = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    """Restore UTC metadata that SQLite does not retain on DateTime columns."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


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
            if current < 3:
                evidence_table.create(connection, checkfirst=True)
                session.execute(
                    insert(schema_migrations_table).values(
                        version=3,
                        name="evidence_links",
                        applied_at=_now(),
                    )
                )
                current = 3
            if current < 4:
                jobs_table.create(connection, checkfirst=True)
                session.execute(
                    insert(schema_migrations_table).values(
                        version=4,
                        name="durable_pipeline_jobs",
                        applied_at=_now(),
                    )
                )
                current = 4
            if current < 5:
                feedback_table.create(connection, checkfirst=True)
                skills_table.create(connection, checkfirst=True)
                post_processors_table.create(connection, checkfirst=True)
                session.execute(
                    insert(schema_migrations_table).values(
                        version=5,
                        name="feedback_skills_post_processors",
                        applied_at=_now(),
                    )
                )
                current = 5
            if current < 6:
                collection_groups_table.create(connection, checkfirst=True)
                collection_group_memberships_table.create(connection, checkfirst=True)
                session.execute(
                    insert(schema_migrations_table).values(
                        version=6,
                        name="collection_groups",
                        applied_at=_now(),
                    )
                )
                current = 6
            if current < 7:
                schema_versions_table.create(connection, checkfirst=True)
                version_columns = (
                    "schema_id",
                    "version",
                    "name",
                    "description",
                    "domain",
                    "purpose",
                    "definition",
                    "system_prompt",
                    "field_descriptions",
                    "review_prompt",
                    "review_trackable",
                    "review_allow_search",
                    "review_search_tools",
                    "post_processors",
                    "created_at",
                    "updated_at",
                )
                session.execute(
                    insert(schema_versions_table).from_select(
                        version_columns,
                        select(
                            schemas_table.c.id,
                            schemas_table.c.version,
                            schemas_table.c.name,
                            schemas_table.c.description,
                            schemas_table.c.domain,
                            schemas_table.c.purpose,
                            schemas_table.c.definition,
                            schemas_table.c.system_prompt,
                            schemas_table.c.field_descriptions,
                            schemas_table.c.review_prompt,
                            schemas_table.c.review_trackable,
                            schemas_table.c.review_allow_search,
                            schemas_table.c.review_search_tools,
                            schemas_table.c.post_processors,
                            schemas_table.c.created_at,
                            schemas_table.c.updated_at,
                        ).where(
                            ~select(schema_versions_table.c.schema_id)
                            .where(
                                schema_versions_table.c.schema_id
                                == schemas_table.c.id,
                                schema_versions_table.c.version
                                == schemas_table.c.version,
                            )
                            .exists()
                        ),
                    )
                )
                session.execute(
                    insert(schema_migrations_table).values(
                        version=7,
                        name="historical_extraction_schemas",
                        applied_at=_now(),
                    )
                )
                current = 7
            if current < 8:
                source_columns = {
                    column["name"]
                    for column in sa_inspect(connection).get_columns("mkb_sources")
                }
                if "uri" not in source_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE mkb_sources ADD COLUMN uri VARCHAR"
                    )
                legacy_sources = session.execute(
                    select(sources_table.c.id, sources_table.c.metadata)
                ).all()
                for source_id, metadata in legacy_sources:
                    external_uri = (
                        metadata.get("_mkb_external_uri")
                        if isinstance(metadata, dict)
                        else None
                    )
                    if isinstance(external_uri, str):
                        session.execute(
                            update(sources_table)
                            .where(sources_table.c.id == source_id)
                            .values(uri=external_uri)
                        )
                session.execute(
                    insert(schema_migrations_table).values(
                        version=8,
                        name="source_external_uri",
                        applied_at=_now(),
                    )
                )
                current = 8
            return int(current)

    def version(self) -> int | None:
        with self._database.session() as session:
            try:
                value = session.execute(
                    select(func.max(schema_migrations_table.c.version))
                ).scalar_one()
            except Exception:
                return None
            if value is None:
                return None
            version = int(value)
            if version > CURRENT_SCHEMA_VERSION:
                raise ConflictError(
                    f"Database schema version {version} is newer than this SDK supports"
                )
            return version


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
            group_id=(uuid.UUID(values["group_id"]) if values.get("group_id") else None),
            metadata=dict(values["metadata"] or {}),
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
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
        group_id = (
            select(collection_group_memberships_table.c.group_id)
            .where(
                collection_group_memberships_table.c.collection_id
                == collections_table.c.id
            )
            .correlate(collections_table)
            .scalar_subquery()
            .label("group_id")
        )
        statement = select(collections_table, source_count, group_id).where(
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
        group_id = (
            select(collection_group_memberships_table.c.group_id)
            .where(
                collection_group_memberships_table.c.collection_id
                == collections_table.c.id
            )
            .correlate(collections_table)
            .scalar_subquery()
            .label("group_id")
        )
        statement = (
            select(collections_table, source_count, group_id)
            .order_by(collections_table.c.created_at.desc(), collections_table.c.id)
            .limit(limit)
            .offset(offset)
        )
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]

    def update(
        self,
        collection_id: uuid.UUID,
        *,
        name: str | None = None,
        source_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Collection:
        current = self.get(collection_id)
        if current is None:
            raise NotFoundError(f"Collection not found: {collection_id}")
        changes = {"updated_at": _now()}
        if name is not None:
            changes["name"] = name
        if source_path is not None:
            changes["source_path"] = source_path
        if metadata is not None:
            changes["metadata"] = _json_value(dict(metadata), "metadata")
        try:
            with self._session(write=True) as session:
                session.execute(
                    update(collections_table)
                    .where(collections_table.c.id == str(collection_id))
                    .values(**changes)
                )
        except IntegrityError as exc:
            raise ConflictError(f"Collection already exists: {name}") from exc
        return self.get(collection_id)

    def delete(self, collection_id: uuid.UUID) -> None:
        with self._session(write=True) as session:
            exists = session.execute(
                select(collections_table.c.id).where(
                    collections_table.c.id == str(collection_id)
                )
            ).scalar_one_or_none()
            if exists is None:
                raise NotFoundError(f"Collection not found: {collection_id}")
            source_count = session.execute(
                select(func.count()).select_from(collection_sources_table).where(
                    collection_sources_table.c.collection_id == str(collection_id)
                )
            ).scalar_one()
            record_count = session.execute(
                select(func.count()).select_from(records_table).where(
                    records_table.c.collection_id == str(collection_id)
                )
            ).scalar_one()
            if source_count or record_count:
                raise ConflictError(
                    "Collection is not empty; remove linked sources and records first"
                )
            session.execute(
                delete(collection_group_memberships_table).where(
                    collection_group_memberships_table.c.collection_id
                    == str(collection_id)
                )
            )
            session.execute(
                delete(collections_table).where(
                    collections_table.c.id == str(collection_id)
                )
            )


class GenericCollectionGroupRepository(_Repository):
    """Portable collection grouping and membership persistence."""

    @staticmethod
    def _model(row) -> CollectionGroup:
        values = row._mapping
        return CollectionGroup(
            id=uuid.UUID(values["id"]),
            name=values["name"],
            description=values["description"],
            color=values["color"],
            display_order=int(values["display_order"]),
            collection_count=int(values.get("collection_count", 0)),
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
        )

    @staticmethod
    def _count():
        return (
            select(func.count())
            .select_from(collection_group_memberships_table)
            .where(
                collection_group_memberships_table.c.group_id
                == collection_groups_table.c.id
            )
            .correlate(collection_groups_table)
            .scalar_subquery()
            .label("collection_count")
        )

    def create(self, group: CollectionGroup) -> CollectionGroup:
        values = group.model_dump(mode="python", exclude={"collection_count"})
        values["id"] = str(group.id)
        try:
            with self._session(write=True) as session:
                session.execute(insert(collection_groups_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Collection group already exists: {group.id}") from exc
        return group

    def get(self, group_id: uuid.UUID) -> CollectionGroup | None:
        statement = select(collection_groups_table, self._count()).where(
            collection_groups_table.c.id == str(group_id)
        )
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[CollectionGroup]:
        statement = (
            select(collection_groups_table, self._count())
            .order_by(
                collection_groups_table.c.display_order,
                collection_groups_table.c.created_at,
                collection_groups_table.c.id,
            )
            .limit(limit)
            .offset(offset)
        )
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]

    def update(self, group_id: uuid.UUID, **changes: Any) -> CollectionGroup:
        values = {key: value for key, value in changes.items() if value is not None}
        values["updated_at"] = _now()
        with self._session(write=True) as session:
            result = session.execute(
                update(collection_groups_table)
                .where(collection_groups_table.c.id == str(group_id))
                .values(**values)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Collection group not found: {group_id}")
        return self.get(group_id)

    def delete(self, group_id: uuid.UUID) -> int:
        with self._session(write=True) as session:
            memberships = session.execute(
                delete(collection_group_memberships_table).where(
                    collection_group_memberships_table.c.group_id == str(group_id)
                )
            ).rowcount
            result = session.execute(
                delete(collection_groups_table).where(
                    collection_groups_table.c.id == str(group_id)
                )
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Collection group not found: {group_id}")
            return int(memberships or 0)

    def assign(
        self,
        collection_ids: list[uuid.UUID],
        group_id: uuid.UUID | None,
    ) -> int:
        identifiers = [str(item) for item in collection_ids]
        with self._session(write=True) as session:
            session.execute(
                delete(collection_group_memberships_table).where(
                    collection_group_memberships_table.c.collection_id.in_(identifiers)
                )
            )
            if group_id is not None:
                session.execute(
                    insert(collection_group_memberships_table),
                    [
                        {"collection_id": identifier, "group_id": str(group_id)}
                        for identifier in identifiers
                    ],
                )
        return len(identifiers)


class GenericSourceRepository(_Repository):
    """Portable metadata and collection membership for object-backed sources."""

    @staticmethod
    def _model(row, collection_ids: tuple[uuid.UUID, ...]) -> Source:
        values = row._mapping
        metadata = dict(values["metadata"] or {})
        return Source(
            id=uuid.UUID(values["id"]),
            filename=values["filename"],
            media_type=values["media_type"],
            size=values["size"],
            sha256=values["sha256"],
            status=values["status"],
            storage=(
                StorageReference(bucket=values["bucket"], key=values["object_key"])
                if values["bucket"] and values["object_key"]
                else None
            ),
            uri=values["uri"],
            collection_ids=collection_ids,
            metadata=metadata,
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
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
            "bucket": source.storage.bucket if source.storage is not None else "",
            "object_key": source.storage.key if source.storage is not None else "",
            "uri": source.uri,
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
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
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
            extracted_at=_utc(values["extracted_at"]),
            source_metadata=dict(values["source_metadata"] or {}),
            annotations=dict(values["annotations"] or {}),
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
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

    def query(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        statement = select(records_table)
        if collection_id is not None:
            statement = statement.where(
                records_table.c.collection_id == str(collection_id)
            )
        if status is not None:
            statement = statement.where(records_table.c.status == status)
        statement = statement.order_by(
            records_table.c.created_at.desc(), records_table.c.id
        )
        with self._session() as session:
            rows = (self._model(row) for row in session.execute(statement))
            matched = (
                record
                for record in rows
                if isinstance(record.data, dict)
                and all(record.data.get(key) == value for key, value in (filters or {}).items())
            )
            return list(matched)[offset : offset + limit]


class GenericExtractionSchemaRepository(_Repository):
    """Portable schema registry for custom extraction definitions."""

    @staticmethod
    def _model(row) -> ExtractionSchema:
        values = row._mapping
        return ExtractionSchema(
            id=uuid.UUID(
                values["id"] if "id" in values else values["schema_id"]
            ),
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
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
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
                session.execute(
                    insert(schema_versions_table).values(
                        schema_id=values["id"],
                        **{key: value for key, value in values.items() if key != "id"},
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(f"Extraction schema already exists: {name}") from exc
        return self.get(identifier)

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

    def get_version(
        self, schema_id: str | uuid.UUID, version: int
    ) -> ExtractionSchema | None:
        statement = select(schema_versions_table).where(
            schema_versions_table.c.schema_id == str(schema_id),
            schema_versions_table.c.version == version,
        )
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

    def list_versions(
        self, schema_id: str | uuid.UUID, *, limit: int = 100, offset: int = 0
    ) -> list[ExtractionSchema]:
        statement = (
            select(schema_versions_table)
            .where(schema_versions_table.c.schema_id == str(schema_id))
            .order_by(schema_versions_table.c.version.desc())
            .limit(limit)
            .offset(offset)
        )
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ExtractionSchema]:
        statement = select(schemas_table).order_by(schemas_table.c.name).limit(limit).offset(offset)
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]

    def update(self, schema_id: uuid.UUID, **changes: Any) -> ExtractionSchema:
        values = {key: value for key, value in changes.items() if value is not None}
        for field in ("definition", "field_descriptions"):
            if field in values:
                values[field] = _json_value(dict(values[field]), field)
        try:
            with self._session(write=True) as session:
                current_row = session.execute(
                    select(schemas_table)
                    .where(schemas_table.c.id == str(schema_id))
                    .with_for_update()
                ).one_or_none()
                if current_row is None:
                    raise NotFoundError(
                        f"Extraction schema not found: {schema_id}"
                    )
                current = dict(current_row._mapping)
                values["version"] = int(current["version"]) + 1
                values["updated_at"] = _now()
                session.execute(
                    update(schemas_table)
                    .where(schemas_table.c.id == str(schema_id))
                    .values(**values)
                )
                version_values = {**current, **values}
                session.execute(
                    insert(schema_versions_table).values(
                        schema_id=version_values["id"],
                        **{
                            key: value
                            for key, value in version_values.items()
                            if key != "id"
                        },
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(
                f"Extraction schema already exists: {values.get('name')}"
            ) from exc
        return self.get(schema_id)

    def delete(self, schema_id: uuid.UUID) -> None:
        with self._session(write=True) as session:
            exists = session.execute(
                select(schemas_table.c.id).where(schemas_table.c.id == str(schema_id))
            ).scalar_one_or_none()
            if exists is None:
                raise NotFoundError(f"Extraction schema not found: {schema_id}")
            projection_count = session.execute(
                select(func.count()).select_from(projections_table).where(
                    projections_table.c.schema_id == str(schema_id)
                )
            ).scalar_one()
            if projection_count:
                raise ConflictError(
                    "Extraction schema is in use; referenced schemas cannot be deleted"
                )
            session.execute(
                delete(schema_versions_table).where(
                    schema_versions_table.c.schema_id == str(schema_id)
                )
            )
            session.execute(
                delete(schemas_table).where(schemas_table.c.id == str(schema_id))
            )


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
            extracted_at=_utc(values["extracted_at"]),
            schema_version=values["schema_version"],
            review_count=values["review_count"],
            review_notes=values["review_notes"],
            reviewed_at=_utc(values["reviewed_at"]),
            deleted_at=_utc(values["deleted_at"]),
            superseded_by_id=(
                uuid.UUID(values["superseded_by_id"])
                if values["superseded_by_id"]
                else None
            ),
            supersedes_ids=tuple(values["supersedes_ids"] or ()),
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
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


class GenericEvidenceRepository(_Repository):
    """Portable lossless evidence-link persistence."""

    @staticmethod
    def _model(row) -> Evidence:
        values = row._mapping
        return Evidence(
            id=uuid.UUID(values["id"]),
            output_type=values["output_type"],
            output_id=uuid.UUID(values["output_id"]),
            source_id=(uuid.UUID(values["source_id"]) if values["source_id"] else None),
            artifact_id=(
                uuid.UUID(values["artifact_id"]) if values["artifact_id"] else None
            ),
            locator=dict(values["locator"] or {}),
            excerpt=values["excerpt"],
            metadata=dict(values["metadata"] or {}),
            created_at=_utc(values["created_at"]),
        )

    def create(
        self,
        *,
        output_type: str,
        output_id: uuid.UUID,
        source_id: uuid.UUID | None = None,
        artifact_id: uuid.UUID | None = None,
        locator: dict[str, Any] | None = None,
        excerpt: str | None = None,
        metadata: dict[str, Any] | None = None,
        evidence_id: uuid.UUID | None = None,
    ) -> Evidence:
        identifier = evidence_id or uuid.uuid4()
        now = _now()
        values = {
            "id": str(identifier),
            "output_type": output_type,
            "output_id": str(output_id),
            "source_id": str(source_id) if source_id is not None else None,
            "artifact_id": str(artifact_id) if artifact_id is not None else None,
            "locator": _json_value(dict(locator or {}), "locator"),
            "excerpt": excerpt,
            "metadata": _json_value(dict(metadata or {}), "metadata"),
            "created_at": now,
        }
        try:
            with self._session(write=True) as session:
                session.execute(insert(evidence_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Evidence already exists: {identifier}") from exc
        return Evidence(
            id=identifier,
            output_type=output_type,
            output_id=output_id,
            source_id=source_id,
            artifact_id=artifact_id,
            locator=values["locator"],
            excerpt=excerpt,
            metadata=values["metadata"],
            created_at=now,
        )

    def get(self, evidence_id: str | uuid.UUID) -> Evidence | None:
        statement = select(evidence_table).where(evidence_table.c.id == str(evidence_id))
        with self._session() as session:
            row = session.execute(statement).one_or_none()
            return self._model(row) if row else None

    def list(
        self,
        *,
        output_id: str | uuid.UUID | None = None,
        source_id: str | uuid.UUID | None = None,
        artifact_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Evidence]:
        statement = select(evidence_table)
        for column, value in (
            (evidence_table.c.output_id, output_id),
            (evidence_table.c.source_id, source_id),
            (evidence_table.c.artifact_id, artifact_id),
        ):
            if value is not None:
                statement = statement.where(column == str(value))
        statement = statement.order_by(
            evidence_table.c.created_at.desc(), evidence_table.c.id
        ).limit(limit).offset(offset)
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]


class GenericFeedbackRepository(_Repository):
    """Portable feedback persistence for SDK-managed databases."""

    @staticmethod
    def _model(row) -> FeedbackItem:
        values = row._mapping
        return FeedbackItem(
            id=uuid.UUID(values["id"]),
            target_record_id=uuid.UUID(values["target_record_id"]),
            target_collection_id=uuid.UUID(values["target_collection_id"]),
            category=values["category"],
            question=values["question"],
            source_agent=values["source_agent"],
            source_projection_id=(
                uuid.UUID(values["source_projection_id"])
                if values["source_projection_id"]
                else None
            ),
            field_path=values["field_path"],
            context=values["context"],
            status=values["status"],
            resolution_notes=values["resolution_notes"],
            resolved_by=values["resolved_by"],
            resolved_at=_utc(values["resolved_at"]),
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
        )

    def create(self, item: FeedbackItem) -> FeedbackItem:
        values = item.model_dump(mode="python")
        values["id"] = str(item.id)
        values["target_record_id"] = str(item.target_record_id)
        values["target_collection_id"] = str(item.target_collection_id)
        values["source_projection_id"] = (
            str(item.source_projection_id) if item.source_projection_id else None
        )
        try:
            with self._session(write=True) as session:
                session.execute(insert(feedback_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Feedback already exists: {item.id}") from exc
        return item

    def get(self, feedback_id: uuid.UUID) -> FeedbackItem | None:
        with self._session() as session:
            row = session.execute(
                select(feedback_table).where(feedback_table.c.id == str(feedback_id))
            ).one_or_none()
            return self._model(row) if row else None

    def list(
        self,
        *,
        collection_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FeedbackItem]:
        statement = select(feedback_table)
        if collection_id is not None:
            statement = statement.where(
                feedback_table.c.target_collection_id == str(collection_id)
            )
        if status is not None:
            statement = statement.where(feedback_table.c.status == status.upper())
        statement = statement.order_by(
            feedback_table.c.created_at.desc(), feedback_table.c.id
        ).limit(limit).offset(offset)
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]

    def update(self, feedback_id: uuid.UUID, **changes: Any) -> FeedbackItem:
        changes["updated_at"] = _now()
        with self._session(write=True) as session:
            result = session.execute(
                update(feedback_table)
                .where(feedback_table.c.id == str(feedback_id))
                .values(**changes)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Feedback not found: {feedback_id}")
        return self.get(feedback_id)


class GenericSkillRepository(_Repository):
    """Portable skill-document persistence."""

    @staticmethod
    def _model(row) -> Skill:
        values = row._mapping
        return Skill(
            id=uuid.UUID(values["id"]),
            name=values["name"],
            slug=values["slug"],
            content=values["content"],
            description=values["description"],
            metadata=dict(values["metadata"] or {}),
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
        )

    def create(self, skill: Skill) -> Skill:
        values = skill.model_dump(mode="python")
        values["id"] = str(skill.id)
        values["metadata"] = _json_value(values["metadata"], "metadata")
        try:
            with self._session(write=True) as session:
                session.execute(insert(skills_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Skill already exists: {skill.slug}") from exc
        return skill

    def get(self, identifier: str) -> Skill | None:
        try:
            uuid.UUID(identifier)
            condition = skills_table.c.id == identifier
        except ValueError:
            condition = skills_table.c.slug == identifier
        with self._session() as session:
            row = session.execute(select(skills_table).where(condition)).one_or_none()
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Skill]:
        statement = select(skills_table).order_by(skills_table.c.name, skills_table.c.id)
        with self._session() as session:
            return [
                self._model(row)
                for row in session.execute(statement.limit(limit).offset(offset))
            ]

    def delete(self, skill_id: uuid.UUID) -> None:
        with self._session(write=True) as session:
            result = session.execute(
                delete(skills_table).where(skills_table.c.id == str(skill_id))
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Skill not found: {skill_id}")


class GenericPostProcessorRepository(_Repository):
    """Portable post-processor source persistence."""

    @staticmethod
    def _model(row) -> PostProcessor:
        values = row._mapping
        return PostProcessor(
            id=uuid.UUID(values["id"]),
            name=values["name"],
            filename=values["filename"],
            source=values["source"],
            metadata=dict(values["metadata"] or {}),
            created_at=_utc(values["created_at"]),
            updated_at=_utc(values["updated_at"]),
        )

    def create(self, processor: PostProcessor) -> PostProcessor:
        values = processor.model_dump(mode="python")
        values["id"] = str(processor.id)
        values["metadata"] = _json_value(values["metadata"], "metadata")
        try:
            with self._session(write=True) as session:
                session.execute(insert(post_processors_table).values(**values))
        except IntegrityError as exc:
            raise ConflictError(f"Post-processor already exists: {processor.id}") from exc
        return processor

    def get(self, processor_id: uuid.UUID) -> PostProcessor | None:
        with self._session() as session:
            row = session.execute(
                select(post_processors_table).where(
                    post_processors_table.c.id == str(processor_id)
                )
            ).one_or_none()
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[PostProcessor]:
        statement = select(post_processors_table).order_by(
            post_processors_table.c.name, post_processors_table.c.id
        )
        with self._session() as session:
            return [
                self._model(row)
                for row in session.execute(statement.limit(limit).offset(offset))
            ]

    def delete(self, processor_id: uuid.UUID) -> None:
        with self._session(write=True) as session:
            result = session.execute(
                delete(post_processors_table).where(
                    post_processors_table.c.id == str(processor_id)
                )
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Post-processor not found: {processor_id}")


class GenericJobBackend(_Repository):
    """Portable persisted backend for local pipeline submission."""

    capabilities = frozenset({Capabilities.DURABLE_SUBMISSION})
    terminal_statuses = frozenset({"COMPLETED", "FAILED", "CANCELLED"})

    @staticmethod
    def _model(row) -> Job:
        values = row._mapping
        return Job(
            id=uuid.UUID(values["id"]),
            kind=values["kind"],
            status=values["status"],
            label=values["label"],
            pipeline_name=values["pipeline_name"],
            pipeline_version=values["pipeline_version"],
            run_id=uuid.UUID(values["run_id"]) if values["run_id"] else None,
            idempotency_key=values["idempotency_key"],
            inputs=dict(values["inputs"] or {}),
            parameters=dict(values["parameters"] or {}),
            checkpoint=dict(values["checkpoint"] or {}),
            events=tuple(values["events"] or ()),
            attempt_count=int(values["attempt_count"]),
            progress=(float(values["progress"]) if values["progress"] is not None else None),
            message=values["message"],
            result=values["result"],
            error=values["error"],
            created_at=_utc(values["created_at"]),
            started_at=_utc(values["started_at"]),
            completed_at=_utc(values["completed_at"]),
        )

    @staticmethod
    def _values(job: Job) -> dict[str, Any]:
        return {
            "id": str(job.id),
            "kind": job.kind,
            "status": job.status,
            "label": job.label,
            "pipeline_name": job.pipeline_name,
            "pipeline_version": job.pipeline_version,
            "run_id": str(job.run_id) if job.run_id else None,
            "idempotency_key": job.idempotency_key,
            "inputs": _json_value(dict(job.inputs), "job inputs"),
            "parameters": _json_value(dict(job.parameters), "job parameters"),
            "checkpoint": _json_value(dict(job.checkpoint), "job checkpoint"),
            "events": _json_value(list(job.events), "job events"),
            "attempt_count": job.attempt_count,
            "progress": str(job.progress) if job.progress is not None else None,
            "message": job.message,
            "result": _json_value(job.result, "job result") if job.result is not None else None,
            "error": job.error,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
        }

    def create(self, job: Job) -> Job:
        try:
            with self._session(write=True) as session:
                session.execute(insert(jobs_table).values(**self._values(job)))
        except IntegrityError as exc:
            if job.idempotency_key is not None:
                with self._session() as session:
                    row = session.execute(
                        select(jobs_table).where(
                            jobs_table.c.idempotency_key == job.idempotency_key
                        )
                    ).one_or_none()
                    if row is not None:
                        return self._model(row)
            raise ConflictError(f"Job already exists: {job.id}") from exc
        return job

    def update(self, job_id: str, **changes: Any) -> Job:
        current = self.get(job_id)
        if current is None:
            raise NotFoundError(f"Job not found: {job_id}")
        updated = current.model_copy(update=changes)
        serialized = self._values(updated)
        values = {name: serialized[name] for name in changes if name in serialized}
        if not values:
            return current
        with self._session(write=True) as session:
            result = session.execute(
                update(jobs_table).where(jobs_table.c.id == str(current.id)).values(**values)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Job not found: {job_id}")
        return self.get(job_id)

    def get(self, job_id: str) -> Job | None:
        try:
            identifier = str(uuid.UUID(str(job_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("job_id must be a UUID") from exc
        with self._session() as session:
            row = session.execute(
                select(jobs_table).where(jobs_table.c.id == identifier)
            ).one_or_none()
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Job]:
        statement = (
            select(jobs_table)
            .order_by(jobs_table.c.created_at.desc(), jobs_table.c.id)
            .limit(limit)
            .offset(offset)
        )
        with self._session() as session:
            return [self._model(row) for row in session.execute(statement)]

    def wait(self, job_id: str, *, timeout: float | None = None) -> Job:
        started = time.monotonic()
        while True:
            job = self.get(job_id)
            if job is None:
                raise NotFoundError(f"Job not found: {job_id}")
            if job.status in self.terminal_statuses:
                return job
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError(f"Timed out waiting for job {job_id}")
            time.sleep(0.02)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise NotFoundError(f"Job not found: {job_id}")
        if job.status in self.terminal_statuses:
            return job
        status = "CANCELLED" if job.status == "QUEUED" else "CANCELLING"
        return self.update(
            job_id,
            status=status,
            message="Cancellation requested",
            completed_at=(_now() if status == "CANCELLED" else None),
        )

    def recover_interrupted(self) -> int:
        """Explicitly mark jobs abandoned by a prior process as resumable."""
        with self._session() as session:
            identifiers = list(
                session.scalars(
                    select(jobs_table.c.id).where(
                        jobs_table.c.status.in_({"QUEUED", "RUNNING", "CANCELLING"})
                    )
                )
            )
        for identifier in identifiers:
            self.update(
                identifier,
                status="INTERRUPTED",
                message="Interrupted by process restart",
                error="Worker process ended before completion",
                completed_at=_now(),
            )
        return len(identifiers)

    def close(self) -> None:
        return None
