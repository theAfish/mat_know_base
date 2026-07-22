"""Repositories that expose the existing MKB schema through typed SDK models."""

from __future__ import annotations

import uuid
import shutil
from pathlib import Path

from sqlalchemy import delete, func, select, update

from mkb.models import (
    Artifact,
    Collection,
    CollectionGroup,
    ExtractionSchema,
    FeedbackItem,
    PostProcessor,
    Projection,
    Record,
    Source,
    StorageReference,
    Skill,
    WorkflowRecord,
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

    def update(
        self,
        collection_id: uuid.UUID,
        *,
        name: str | None = None,
        source_path: str | None = None,
        metadata: dict | None = None,
    ) -> Collection:
        from mkb.db.models import ResearchProject
        from mkb.exceptions import NotFoundError

        with self._database.transaction() as session:
            row = session.get(ResearchProject, collection_id)
            if row is None:
                raise NotFoundError(f"Collection not found: {collection_id}")
            if name is not None:
                row.label = name
            if source_path is not None:
                row.source_path = source_path
            if metadata is not None:
                row.metadata_ = dict(metadata)
            session.flush()
            return self._model(row)


class SQLAlchemyCollectionGroupRepository:
    """Map collection groups to existing project-group rows without copying them."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row, collection_count: int = 0) -> CollectionGroup:
        return CollectionGroup(
            id=row.group_id,
            name=row.name,
            description=row.description,
            color=row.color,
            display_order=row.display_order,
            collection_count=collection_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _count(self, session, group_id: uuid.UUID) -> int:
        from mkb.db.models import ResearchProject

        return int(
            session.scalar(
                select(func.count()).select_from(ResearchProject).where(
                    ResearchProject.group_id == group_id
                )
            )
            or 0
        )

    def create(self, group: CollectionGroup) -> CollectionGroup:
        from mkb.db.models import ProjectGroup

        row = ProjectGroup(
            group_id=group.id,
            name=group.name,
            description=group.description,
            color=group.color,
            display_order=group.display_order,
        )
        with self._database.transaction() as session:
            session.add(row)
        return group

    def get(self, group_id: uuid.UUID) -> CollectionGroup | None:
        from mkb.db.models import ProjectGroup

        with self._database.session() as session:
            row = session.get(ProjectGroup, group_id)
            return self._model(row, self._count(session, group_id)) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[CollectionGroup]:
        from mkb.db.models import ProjectGroup, ResearchProject

        count = (
            select(func.count())
            .select_from(ResearchProject)
            .where(ResearchProject.group_id == ProjectGroup.group_id)
            .correlate(ProjectGroup)
            .scalar_subquery()
        )
        statement = (
            select(ProjectGroup, count)
            .order_by(
                ProjectGroup.display_order,
                ProjectGroup.created_at,
                ProjectGroup.group_id,
            )
            .limit(limit)
            .offset(offset)
        )
        with self._database.session() as session:
            return [self._model(row, int(total)) for row, total in session.execute(statement)]

    def update(self, group_id: uuid.UUID, **changes) -> CollectionGroup:
        from mkb.db.models import ProjectGroup

        values = {key: value for key, value in changes.items() if value is not None}
        with self._database.transaction() as session:
            result = session.execute(
                update(ProjectGroup)
                .where(ProjectGroup.group_id == group_id)
                .values(**values)
            )
            if result.rowcount == 0:
                from mkb.exceptions import NotFoundError

                raise NotFoundError(f"Collection group not found: {group_id}")
        return self.get(group_id)

    def delete(self, group_id: uuid.UUID) -> int:
        from mkb.db.models import ProjectGroup, ResearchProject
        from mkb.exceptions import NotFoundError

        with self._database.transaction() as session:
            unassigned = session.execute(
                update(ResearchProject)
                .where(ResearchProject.group_id == group_id)
                .values(group_id=None)
            ).rowcount
            result = session.execute(
                delete(ProjectGroup).where(ProjectGroup.group_id == group_id)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Collection group not found: {group_id}")
        return int(unassigned or 0)

    def assign(
        self,
        collection_ids: list[uuid.UUID],
        group_id: uuid.UUID | None,
    ) -> int:
        from mkb.db.models import ResearchProject

        with self._database.transaction() as session:
            result = session.execute(
                update(ResearchProject)
                .where(ResearchProject.project_id.in_(collection_ids))
                .values(group_id=group_id)
            )
            return int(result.rowcount or 0)


class SQLAlchemyFeedbackRepository:
    """Read and update existing feedback rows through an injected database."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row) -> FeedbackItem:
        status = _enum_value(row.status)
        return FeedbackItem(
            id=row.feedback_id,
            target_record_id=row.target_frame_id,
            target_collection_id=row.target_project_id,
            category=row.category,
            question=row.question,
            source_agent=row.source_agent,
            source_projection_id=row.source_projection_id,
            field_path=row.field_path,
            context=row.context,
            status="IN_REVIEW" if status == "ACKNOWLEDGED" else status,
            resolution_notes=row.resolution_notes,
            resolved_by=row.resolved_by,
            resolved_at=row.resolved_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def create(self, item: FeedbackItem) -> FeedbackItem:
        from mkb.db.models import Feedback as FeedbackRow, FeedbackStatus

        row = FeedbackRow(
            feedback_id=item.id,
            source_projection_id=item.source_projection_id,
            source_agent=item.source_agent,
            target_frame_id=item.target_record_id,
            target_project_id=item.target_collection_id,
            category=item.category,
            field_path=item.field_path,
            question=item.question,
            context=item.context,
            status=FeedbackStatus.OPEN,
        )
        with self._database.transaction() as session:
            session.add(row)
        return item

    def get(self, feedback_id: uuid.UUID) -> FeedbackItem | None:
        from mkb.db.models import Feedback as FeedbackRow

        with self._database.session() as session:
            row = session.get(FeedbackRow, feedback_id)
            return self._model(row) if row else None

    def list(
        self,
        *,
        collection_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FeedbackItem]:
        from mkb.db.models import Feedback as FeedbackRow, FeedbackStatus

        statement = select(FeedbackRow)
        if collection_id is not None:
            statement = statement.where(
                FeedbackRow.target_project_id == collection_id
            )
        if status is not None:
            persisted_status = "ACKNOWLEDGED" if status.upper() == "IN_REVIEW" else status.upper()
            statement = statement.where(FeedbackRow.status == FeedbackStatus(persisted_status))
        statement = statement.order_by(
            FeedbackRow.created_at.desc(), FeedbackRow.feedback_id
        ).limit(limit).offset(offset)
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]

    def update(self, feedback_id: uuid.UUID, **changes) -> FeedbackItem:
        from mkb.db.models import Feedback as FeedbackRow, FeedbackStatus
        from mkb.exceptions import NotFoundError

        values = dict(changes)
        if "status" in values:
            status = values["status"]
            values["status"] = FeedbackStatus(
                "ACKNOWLEDGED" if status == "IN_REVIEW" else status
            )
        with self._database.transaction() as session:
            result = session.execute(
                update(FeedbackRow)
                .where(FeedbackRow.feedback_id == feedback_id)
                .values(**values)
            )
            if result.rowcount == 0:
                raise NotFoundError(f"Feedback not found: {feedback_id}")
        return self.get(feedback_id)


class SQLAlchemySkillRepository:
    """Map typed skills to existing custom-skill rows and files."""

    def __init__(self, database: Database, root: str | Path = "data/skills"):
        self._database = database
        self._root = Path(root)

    @staticmethod
    def _model(row) -> Skill:
        return Skill(
            id=row.skill_id,
            name=row.name,
            slug=row.slug,
            content=row.skill_md,
            description=row.description,
            metadata=dict(row.metadata_ or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def create(self, skill: Skill) -> Skill:
        from mkb.db.models import CustomSkill

        root = self._root / f"{skill.slug}_{skill.id.hex[:8]}"
        root.mkdir(parents=True, exist_ok=False)
        skill_file = root / "SKILL.md"
        skill_file.write_text(skill.content, encoding="utf-8")
        row = CustomSkill(
            skill_id=skill.id,
            name=skill.name,
            slug=skill.slug,
            description=skill.description,
            source_type="sdk",
            storage_path=str(root),
            skill_md=skill.content,
            file_count=1,
            metadata_=skill.metadata,
        )
        try:
            with self._database.transaction() as session:
                session.add(row)
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        return skill

    def get(self, identifier: str) -> Skill | None:
        from mkb.db.models import CustomSkill

        try:
            condition = CustomSkill.skill_id == uuid.UUID(identifier)
        except ValueError:
            condition = CustomSkill.slug == identifier
        with self._database.session() as session:
            row = session.scalar(select(CustomSkill).where(condition))
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Skill]:
        from mkb.db.models import CustomSkill

        statement = (
            select(CustomSkill)
            .order_by(CustomSkill.name, CustomSkill.skill_id)
            .limit(limit)
            .offset(offset)
        )
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]

    def delete(self, skill_id: uuid.UUID) -> None:
        from mkb.db.models import CustomSkill
        from mkb.exceptions import NotFoundError

        with self._database.transaction() as session:
            row = session.get(CustomSkill, skill_id)
            if row is None:
                raise NotFoundError(f"Skill not found: {skill_id}")
            root = Path(row.storage_path)
            session.delete(row)
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)


class SQLAlchemyPostProcessorRepository:
    """Map typed processor source to existing script metadata and files."""

    def __init__(
        self,
        database: Database,
        root: str | Path = "data/post_processor_scripts",
    ):
        self._database = database
        self._root = Path(root)

    @staticmethod
    def _model(row) -> PostProcessor:
        path = Path(row.storage_path)
        source = path.read_text(encoding="utf-8") if path.is_file() else ""
        return PostProcessor(
            id=row.script_id,
            name=row.name,
            filename=row.filename,
            source=source,
            metadata={"content_missing": not path.is_file()},
            created_at=row.created_at,
            updated_at=row.created_at,
        )

    def create(self, processor: PostProcessor) -> PostProcessor:
        from mkb.db.models import PostProcessorScript

        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{processor.id.hex}_{processor.filename}"
        path.write_text(processor.source, encoding="utf-8")
        row = PostProcessorScript(
            script_id=processor.id,
            name=processor.name,
            filename=processor.filename,
            storage_path=str(path),
        )
        try:
            with self._database.transaction() as session:
                session.add(row)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return processor

    def get(self, processor_id: uuid.UUID) -> PostProcessor | None:
        from mkb.db.models import PostProcessorScript

        with self._database.session() as session:
            row = session.get(PostProcessorScript, processor_id)
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[PostProcessor]:
        from mkb.db.models import PostProcessorScript

        statement = (
            select(PostProcessorScript)
            .order_by(PostProcessorScript.name, PostProcessorScript.script_id)
            .limit(limit)
            .offset(offset)
        )
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]

    def delete(self, processor_id: uuid.UUID) -> None:
        from mkb.db.models import PostProcessorScript
        from mkb.exceptions import NotFoundError

        with self._database.transaction() as session:
            row = session.get(PostProcessorScript, processor_id)
            if row is None:
                raise NotFoundError(f"Post-processor not found: {processor_id}")
            path = Path(row.storage_path)
            session.delete(row)
        path.unlink(missing_ok=True)


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

    def create(
        self,
        *,
        name: str,
        domain: str,
        definition: dict,
        system_prompt: str,
        description: str | None = None,
        purpose: str = "freeform",
        field_descriptions: dict | None = None,
        schema_id: uuid.UUID | None = None,
    ) -> ExtractionSchema:
        from sqlalchemy.exc import IntegrityError

        from mkb.db.models import Space
        from mkb.exceptions import ConflictError

        identifier = schema_id or uuid.uuid4()
        row = Space(
            space_id=identifier,
            name=name,
            description=description,
            domain=domain,
            purpose=purpose,
            extraction_schema=definition,
            system_prompt=system_prompt,
            field_descriptions=field_descriptions or {},
            review_trackable=True,
            review_allow_search=False,
            review_search_tools=[],
            post_processors=[],
            version=1,
        )
        try:
            with self._database.transaction() as session:
                session.add(row)
        except IntegrityError as exc:
            raise ConflictError(f"Extraction schema already exists: {name}") from exc
        return self.get(identifier)

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

    def get_version(
        self, schema_id: str | uuid.UUID, version: int
    ) -> ExtractionSchema | None:
        """Resolve the current legacy space revision when its version matches."""
        schema = self.get(schema_id)
        if schema is None or schema.version != version:
            return None
        return schema

    def list_versions(
        self,
        schema_id: str | uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExtractionSchema]:
        schema = self.get(schema_id)
        if schema is None or offset > 0 or limit < 1:
            return []
        return [schema]

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ExtractionSchema]:
        from mkb.db.models import Space

        statement = select(Space).order_by(Space.name, Space.space_id).limit(limit).offset(
            offset
        )
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]

    def update(self, schema_id: uuid.UUID, **changes) -> ExtractionSchema:
        from sqlalchemy.exc import IntegrityError

        from mkb.db.models import Space
        from mkb.exceptions import ConflictError, NotFoundError

        mapping = {
            "definition": "extraction_schema",
            "field_descriptions": "field_descriptions",
        }
        values = {
            mapping.get(key, key): value
            for key, value in changes.items()
            if value is not None
        }
        with self._database.transaction() as session:
            row = session.get(Space, schema_id)
            if row is None:
                raise NotFoundError(f"Extraction schema not found: {schema_id}")
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
            try:
                session.flush()
            except IntegrityError as exc:
                raise ConflictError(
                    f"Extraction schema already exists: {values.get('name')}"
                ) from exc
        return self.get(schema_id)

    def delete(self, schema_id: uuid.UUID) -> None:
        from mkb.db.models import Projection as ProjectionRow, Space
        from mkb.exceptions import ConflictError, NotFoundError

        with self._database.transaction() as session:
            row = session.get(Space, schema_id)
            if row is None:
                raise NotFoundError(f"Extraction schema not found: {schema_id}")
            projection_count = session.scalar(
                select(func.count()).select_from(ProjectionRow).where(
                    ProjectionRow.space_id == schema_id
                )
            )
            if projection_count:
                raise ConflictError(
                    "Extraction schema is in use; referenced schemas cannot be deleted"
                )
            session.delete(row)


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


class SQLAlchemyWorkflowRepository:
    """Read legacy raw workflow rows without normalizing their JSON payloads."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row) -> WorkflowRecord:
        return WorkflowRecord(
            id=row.extraction_id,
            collection_id=row.project_id,
            version=row.version,
            schema_version=row.schema_version,
            extractor_version=row.extractor_version,
            model=row.model,
            status=row.status,
            record_status=row.record_status,
            supersedes_id=row.supersedes_extraction_id,
            correction_reason=row.correction_reason,
            correction_author=row.correction_author,
            correction_details=dict(row.correction_details or {}),
            review_flags=tuple(row.review_flags or ()),
            graph=(dict(row.graph) if row.graph is not None else None),
            checkpoint=(dict(row.checkpoint) if row.checkpoint is not None else None),
            provenance=dict(row.provenance or {}),
            error=row.error,
            extracted_at=row.extracted_at,
            checkpoint_updated_at=row.checkpoint_updated_at,
            created_at=row.created_at,
        )

    def get(self, workflow_id: str | uuid.UUID) -> WorkflowRecord | None:
        from mkb.db.models import RawWorkflowExtraction

        with self._database.session() as session:
            row = session.get(RawWorkflowExtraction, uuid.UUID(str(workflow_id)))
            return self._model(row) if row else None

    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowRecord]:
        from mkb.db.models import RawWorkflowExtraction

        statement = select(RawWorkflowExtraction)
        if collection_id is not None:
            statement = statement.where(
                RawWorkflowExtraction.project_id == uuid.UUID(str(collection_id))
            )
        if status is not None:
            statement = statement.where(RawWorkflowExtraction.status == status)
        statement = statement.order_by(
            RawWorkflowExtraction.created_at.desc(),
            RawWorkflowExtraction.extraction_id,
        ).limit(limit).offset(offset)
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]
