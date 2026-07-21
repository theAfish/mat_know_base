"""Typed grouped services for feedback and consumer-managed extensions."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from mkb.exceptions import ConflictError, NotFoundError, ValidationError
from mkb.models import FeedbackItem, OperationReceipt, PostProcessor, Skill


def _id(value: str | uuid.UUID, field: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError(f"{field} must be a UUID") from exc


def _page(limit: int, offset: int) -> None:
    if limit < 1 or limit > 1000:
        raise ValidationError("limit must be between 1 and 1000")
    if offset < 0:
        raise ValidationError("offset must be non-negative")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ValidationError("skill name must contain letters or numbers")
    return slug


@runtime_checkable
class FeedbackRepository(Protocol):
    def create(self, item: FeedbackItem) -> FeedbackItem: ...
    def get(self, feedback_id: uuid.UUID) -> FeedbackItem | None: ...
    def list(self, **filters: Any) -> list[FeedbackItem]: ...
    def update(self, feedback_id: uuid.UUID, **changes: Any) -> FeedbackItem: ...


@runtime_checkable
class SkillRepository(Protocol):
    def create(self, skill: Skill) -> Skill: ...
    def get(self, identifier: str) -> Skill | None: ...
    def list(self, *, limit: int, offset: int) -> list[Skill]: ...
    def delete(self, skill_id: uuid.UUID) -> None: ...


@runtime_checkable
class PostProcessorRepository(Protocol):
    def create(self, processor: PostProcessor) -> PostProcessor: ...
    def get(self, processor_id: uuid.UUID) -> PostProcessor | None: ...
    def list(self, *, limit: int, offset: int) -> list[PostProcessor]: ...
    def delete(self, processor_id: uuid.UUID) -> None: ...


class Feedback:
    """Create, inspect, review, and resolve feedback items."""

    def __init__(self, repository: FeedbackRepository | None):
        self._repository = repository

    def _repo(self) -> FeedbackRepository:
        if self._repository is None:
            raise ConflictError("Feedback persistence is unavailable for this client")
        return self._repository

    def create(
        self,
        *,
        target_record_id: str | uuid.UUID,
        target_collection_id: str | uuid.UUID,
        category: str,
        question: str,
        source_agent: str = "user",
        source_projection_id: str | uuid.UUID | None = None,
        field_path: str | None = None,
        context: str | None = None,
        feedback_id: str | uuid.UUID | None = None,
    ) -> FeedbackItem:
        if not category.strip() or not question.strip():
            raise ValidationError("feedback category and question must not be empty")
        now = datetime.now(timezone.utc)
        return self._repo().create(
            FeedbackItem(
                id=_id(feedback_id, "feedback_id") if feedback_id else uuid.uuid4(),
                target_record_id=_id(target_record_id, "target_record_id"),
                target_collection_id=_id(target_collection_id, "target_collection_id"),
                category=category.strip(),
                question=question.strip(),
                source_agent=source_agent.strip() or "user",
                source_projection_id=(
                    _id(source_projection_id, "source_projection_id")
                    if source_projection_id
                    else None
                ),
                field_path=field_path,
                context=context,
                created_at=now,
                updated_at=now,
            )
        )

    def get(self, feedback_id: str | uuid.UUID) -> FeedbackItem | None:
        return self._repo().get(_id(feedback_id, "feedback_id"))

    def require(self, feedback_id: str | uuid.UUID) -> FeedbackItem:
        item = self.get(feedback_id)
        if item is None:
            raise NotFoundError(f"Feedback not found: {feedback_id}")
        return item

    def list(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FeedbackItem]:
        _page(limit, offset)
        return self._repo().list(
            collection_id=_id(collection_id, "collection_id") if collection_id else None,
            status=status,
            limit=limit,
            offset=offset,
        )

    def review(self, feedback_id: str | uuid.UUID) -> FeedbackItem:
        item = self.require(feedback_id)
        if item.status not in {"OPEN", "IN_REVIEW"}:
            raise ConflictError(f"Feedback is already terminal: {item.status}")
        return self._repo().update(item.id, status="IN_REVIEW")

    def resolve(
        self,
        feedback_id: str | uuid.UUID,
        *,
        notes: str,
        status: str = "RESOLVED",
        resolved_by: str = "user",
    ) -> FeedbackItem:
        clean_status = status.strip().upper()
        if clean_status not in {"RESOLVED", "DISMISSED", "DEV_ISSUE"}:
            raise ValidationError("feedback resolution status is invalid")
        if not notes.strip():
            raise ValidationError("resolution notes must not be empty")
        item = self.require(feedback_id)
        return self._repo().update(
            item.id,
            status=clean_status,
            resolution_notes=notes,
            resolved_by=resolved_by,
            resolved_at=datetime.now(timezone.utc),
        )


class Skills:
    """Register instruction documents without relying on a module-global registry."""

    def __init__(self, repository: SkillRepository | None):
        self._repository = repository

    def _repo(self) -> SkillRepository:
        if self._repository is None:
            raise ConflictError("Skill persistence is unavailable for this client")
        return self._repository

    def create(
        self,
        *,
        name: str,
        content: str,
        slug: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        skill_id: str | uuid.UUID | None = None,
    ) -> Skill:
        if not name.strip() or not content.strip():
            raise ValidationError("skill name and content must not be empty")
        now = datetime.now(timezone.utc)
        return self._repo().create(
            Skill(
                id=_id(skill_id, "skill_id") if skill_id else uuid.uuid4(),
                name=name.strip(),
                slug=_slug(slug or name),
                content=content,
                description=description,
                metadata=metadata or {},
                created_at=now,
                updated_at=now,
            )
        )

    def get(self, skill_id_or_slug: str | uuid.UUID) -> Skill | None:
        return self._repo().get(str(skill_id_or_slug))

    def require(self, skill_id_or_slug: str | uuid.UUID) -> Skill:
        skill = self.get(skill_id_or_slug)
        if skill is None:
            raise NotFoundError(f"Skill not found: {skill_id_or_slug}")
        return skill

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Skill]:
        _page(limit, offset)
        return self._repo().list(limit=limit, offset=offset)

    def delete(self, skill_id_or_slug: str | uuid.UUID) -> OperationReceipt:
        skill = self.require(skill_id_or_slug)
        self._repo().delete(skill.id)
        return OperationReceipt(
            operation="skill.delete",
            status="COMPLETED",
            resource_type="skill",
            resource_id=skill.id,
            created_at=datetime.now(timezone.utc),
        )


class PostProcessors:
    """Register deterministic processor source; execution remains policy-controlled."""

    def __init__(self, repository: PostProcessorRepository | None):
        self._repository = repository

    def _repo(self) -> PostProcessorRepository:
        if self._repository is None:
            raise ConflictError("Post-processor persistence is unavailable for this client")
        return self._repository

    def register(
        self,
        *,
        name: str,
        source: str,
        filename: str | None = None,
        metadata: dict[str, Any] | None = None,
        processor_id: str | uuid.UUID | None = None,
    ) -> PostProcessor:
        if not name.strip() or not source.strip():
            raise ValidationError("post-processor name and source must not be empty")
        safe_filename = (filename or f"{_slug(name)}.py").strip()
        if not safe_filename.endswith(".py") or "/" in safe_filename or "\\" in safe_filename:
            raise ValidationError("post-processor filename must be a simple .py filename")
        now = datetime.now(timezone.utc)
        return self._repo().create(
            PostProcessor(
                id=_id(processor_id, "processor_id") if processor_id else uuid.uuid4(),
                name=name.strip(),
                filename=safe_filename,
                source=source,
                metadata=metadata or {},
                created_at=now,
                updated_at=now,
            )
        )

    def get(self, processor_id: str | uuid.UUID) -> PostProcessor | None:
        return self._repo().get(_id(processor_id, "processor_id"))

    def require(self, processor_id: str | uuid.UUID) -> PostProcessor:
        processor = self.get(processor_id)
        if processor is None:
            raise NotFoundError(f"Post-processor not found: {processor_id}")
        return processor

    def list(self, *, limit: int = 100, offset: int = 0) -> list[PostProcessor]:
        _page(limit, offset)
        return self._repo().list(limit=limit, offset=offset)

    def delete(self, processor_id: str | uuid.UUID) -> OperationReceipt:
        processor = self.require(processor_id)
        self._repo().delete(processor.id)
        return OperationReceipt(
            operation="post_processor.delete",
            status="COMPLETED",
            resource_type="post_processor",
            resource_id=processor.id,
            created_at=datetime.now(timezone.utc),
        )
