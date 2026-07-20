"""Repositories that expose the existing MKB schema through typed SDK models."""

from __future__ import annotations

import uuid

from sqlalchemy import select

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


class SQLAlchemyCollectionRepository:
    """Read current ``research_projects`` rows without copying or rewriting them."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row) -> Collection:
        return Collection(
            id=row.project_id,
            name=row.label,
            source_path=row.source_path,
            source_count=row.file_count,
            group_id=row.group_id,
            metadata=dict(row.metadata_ or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get(self, collection_id: str | uuid.UUID) -> Collection | None:
        from mkb.db.models import ResearchProject

        identifier = uuid.UUID(str(collection_id))
        with self._database.session() as session:
            row = session.get(ResearchProject, identifier)
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Collection]:
        from mkb.db.models import ResearchProject

        statement = (
            select(ResearchProject)
            .order_by(ResearchProject.created_at.desc(), ResearchProject.project_id)
            .limit(limit)
            .offset(offset)
        )
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]


class SQLAlchemySourceRepository:
    """Read legacy assets and their collection links through an injected database."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row, collection_ids=()) -> Source:
        return Source(
            id=row.asset_id,
            filename=row.filename,
            media_type=row.mime_type,
            size=row.size_bytes,
            sha256=row.sha256,
            status=row.status.value,
            storage=StorageReference(bucket=row.s3_bucket, key=row.s3_key),
            collection_ids=tuple(collection_ids),
            metadata=dict(row.metadata_ or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _collection_ids(self, session, source_ids) -> dict[uuid.UUID, list[uuid.UUID]]:
        from mkb.db.models import ProjectAsset

        result = {source_id: [] for source_id in source_ids}
        if not source_ids:
            return result
        statement = (
            select(ProjectAsset.asset_id, ProjectAsset.project_id)
            .where(ProjectAsset.asset_id.in_(source_ids))
            .order_by(ProjectAsset.asset_id, ProjectAsset.project_id)
        )
        for source_id, collection_id in session.execute(statement):
            result[source_id].append(collection_id)
        return result

    def get(self, source_id: str | uuid.UUID) -> Source | None:
        from mkb.db.models import Asset

        identifier = uuid.UUID(str(source_id))
        with self._database.session() as session:
            row = session.get(Asset, identifier)
            if row is None:
                return None
            links = self._collection_ids(session, [identifier])
            return self._model(row, links[identifier])

    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Source]:
        from mkb.db.models import Asset, ProjectAsset

        statement = select(Asset)
        if collection_id is not None:
            identifier = uuid.UUID(str(collection_id))
            statement = statement.join(
                ProjectAsset, ProjectAsset.asset_id == Asset.asset_id
            ).where(ProjectAsset.project_id == identifier)
        statement = statement.order_by(Asset.created_at.desc(), Asset.asset_id).limit(
            limit
        ).offset(offset)
        with self._database.session() as session:
            rows = list(session.scalars(statement))
            links = self._collection_ids(session, [row.asset_id for row in rows])
            return [self._model(row, links[row.asset_id]) for row in rows]


class SQLAlchemyArtifactRepository:
    """Read legacy processed assets through an injected database."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row) -> Artifact:
        metadata = dict(row.conversion_metadata or {})
        return Artifact(
            id=row.processed_asset_id,
            source_id=row.asset_id,
            processing_type=row.processing_type.value,
            format=row.output_format,
            size=row.size_bytes,
            sha256=row.sha256,
            source_sha256=row.raw_asset_hash,
            storage=StorageReference(bucket=row.s3_bucket, key=row.s3_key),
            primary_path=metadata.get("primary_relpath"),
            metadata=metadata,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get(self, artifact_id: str | uuid.UUID) -> Artifact | None:
        from mkb.db.models import ProcessedAsset

        identifier = uuid.UUID(str(artifact_id))
        with self._database.session() as session:
            row = session.get(ProcessedAsset, identifier)
            return self._model(row) if row else None

    def list(
        self,
        *,
        source_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        from mkb.db.models import ProcessedAsset

        statement = select(ProcessedAsset)
        if source_id is not None:
            statement = statement.where(
                ProcessedAsset.asset_id == uuid.UUID(str(source_id))
            )
        statement = statement.order_by(
            ProcessedAsset.created_at.desc(), ProcessedAsset.processed_asset_id
        ).limit(limit).offset(offset)
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]


def _enum_value(value) -> str:
    return str(getattr(value, "value", value))


class SQLAlchemyRecordRepository:
    """Read current knowledge frames as generic records."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row) -> Record:
        return Record(
            id=row.frame_id,
            collection_id=row.project_id,
            status=_enum_value(row.status),
            data=row.content if row.content is not None else {},
            summary=row.extraction_summary,
            review_count=row.times_checked,
            version=row.extraction_version,
            extracted_at=row.extracted_at,
            source_metadata=dict(row.source_metadata or {}),
            annotations=dict(row.agent_annotations or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get(self, record_id: str | uuid.UUID) -> Record | None:
        from mkb.db.models import KnowledgeFrame

        with self._database.session() as session:
            row = session.get(KnowledgeFrame, uuid.UUID(str(record_id)))
            return self._model(row) if row else None

    def get_for_collection(self, collection_id: str | uuid.UUID) -> Record | None:
        from mkb.db.models import KnowledgeFrame

        statement = select(KnowledgeFrame).where(
            KnowledgeFrame.project_id == uuid.UUID(str(collection_id))
        )
        with self._database.session() as session:
            row = session.scalar(statement)
            return self._model(row) if row else None

    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        from mkb.db.models import KnowledgeFrame

        statement = select(KnowledgeFrame)
        if collection_id is not None:
            statement = statement.where(
                KnowledgeFrame.project_id == uuid.UUID(str(collection_id))
            )
        if status is not None:
            statement = statement.where(KnowledgeFrame.status == status)
        statement = statement.order_by(
            KnowledgeFrame.created_at.desc(), KnowledgeFrame.frame_id
        ).limit(limit).offset(offset)
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]


class SQLAlchemyExtractionSchemaRepository:
    """Read current spaces as generic extraction schema definitions."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row) -> ExtractionSchema:
        return ExtractionSchema(
            id=row.space_id,
            name=row.name,
            description=row.description,
            domain=row.domain,
            purpose=row.purpose,
            definition=dict(row.extraction_schema or {}),
            system_prompt=row.system_prompt,
            field_descriptions=dict(row.field_descriptions or {}),
            review_prompt=row.review_prompt,
            review_trackable=row.review_trackable,
            review_allow_search=row.review_allow_search,
            review_search_tools=tuple(row.review_search_tools or ()),
            post_processors=tuple(row.post_processors or ()),
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get(self, schema_id: str | uuid.UUID) -> ExtractionSchema | None:
        from mkb.db.models import Space

        with self._database.session() as session:
            row = session.get(Space, uuid.UUID(str(schema_id)))
            return self._model(row) if row else None

    def get_by_name(self, name: str) -> ExtractionSchema | None:
        from mkb.db.models import Space

        statement = select(Space).where(Space.name == name)
        with self._database.session() as session:
            row = session.scalar(statement)
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ExtractionSchema]:
        from mkb.db.models import Space

        statement = select(Space).order_by(Space.name, Space.space_id).limit(limit).offset(
            offset
        )
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]


class SQLAlchemyProjectionRepository:
    """Read stored projections and relate them back to their collections."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row, collection_id: uuid.UUID) -> Projection:
        return Projection(
            id=row.projection_id,
            schema_id=row.space_id,
            record_id=row.frame_id,
            collection_id=collection_id,
            source_type=row.source_type,
            status=_enum_value(row.status),
            data=row.data if row.data is not None else {},
            validation=(
                dict(row.validation_result) if row.validation_result is not None else None
            ),
            notes=row.agent_notes,
            extracted_at=row.extracted_at,
            schema_version=row.space_version,
            review_count=row.times_reviewed,
            review_notes=row.review_notes,
            reviewed_at=row.reviewed_at,
            deleted_at=row.deleted_at,
            superseded_by_id=row.superseded_by_id,
            supersedes_ids=tuple(str(value) for value in (row.supersedes_ids or ())),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get(self, projection_id: str | uuid.UUID) -> Projection | None:
        from mkb.db.models import KnowledgeFrame, Projection as ProjectionRow

        statement = (
            select(ProjectionRow, KnowledgeFrame.project_id)
            .join(KnowledgeFrame, KnowledgeFrame.frame_id == ProjectionRow.frame_id)
            .where(ProjectionRow.projection_id == uuid.UUID(str(projection_id)))
        )
        with self._database.session() as session:
            result = session.execute(statement).one_or_none()
            return self._model(*result) if result else None

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
        from mkb.db.models import KnowledgeFrame, Projection as ProjectionRow

        statement = select(ProjectionRow, KnowledgeFrame.project_id).join(
            KnowledgeFrame, KnowledgeFrame.frame_id == ProjectionRow.frame_id
        )
        if schema_id is not None:
            statement = statement.where(
                ProjectionRow.space_id == uuid.UUID(str(schema_id))
            )
        if record_id is not None:
            statement = statement.where(
                ProjectionRow.frame_id == uuid.UUID(str(record_id))
            )
        if collection_id is not None:
            statement = statement.where(
                KnowledgeFrame.project_id == uuid.UUID(str(collection_id))
            )
        if status is not None:
            statement = statement.where(ProjectionRow.status == status)
        if not include_deleted:
            statement = statement.where(ProjectionRow.deleted_at.is_(None))
        if not include_history:
            statement = statement.where(ProjectionRow.superseded_by_id.is_(None))
        statement = statement.order_by(
            ProjectionRow.created_at.desc(), ProjectionRow.projection_id
        )
        with self._database.session() as session:
            rows = list(session.execute(statement))

        models = [self._model(*row) for row in rows]
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
