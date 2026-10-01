"""Application service for reviewed drafts and immutable published facts."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from mkb.exceptions import ConflictError, ValidationError
from mkb.models import DraftGraph, DraftRevision, FactRevision, OutboxEvent, ReviewDecision


class KnowledgeRepository(Protocol):
    def create_draft(self, **values: Any) -> tuple[DraftGraph, DraftRevision]: ...
    def create_correction_request(self, **values: Any) -> tuple[DraftGraph, DraftRevision]: ...
    def get_draft(self, draft_id: uuid.UUID) -> DraftGraph | None: ...
    def list_drafts(
        self,
        *,
        collection_id: uuid.UUID | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> list[DraftGraph]: ...
    def get_revision(self, draft_id: uuid.UUID, revision: int | None = None) -> DraftRevision | None: ...
    def revise(self, **values: Any) -> DraftRevision: ...
    def submit_review(self, **values: Any) -> ReviewDecision: ...
    def reject(self, **values: Any) -> ReviewDecision: ...
    def approve(self, **values: Any) -> tuple[ReviewDecision, FactRevision, OutboxEvent]: ...
    def get_fact(self, fact_id: uuid.UUID) -> FactRevision | None: ...
    def list_facts(self, *, collection_id: uuid.UUID | None, limit: int, offset: int) -> list[FactRevision]: ...
    def list_events(self, *, limit: int, offset: int) -> list[OutboxEvent]: ...


def _identifier(value: str | uuid.UUID, name: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError(f"{name} must be a UUID") from exc


def _required_text(value: str, name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValidationError(f"{name} must not be empty")
    return clean


class Knowledge:
    """Review-gated lifecycle for draft graphs and published fact revisions."""

    def __init__(self, repository: KnowledgeRepository | None):
        self._repository = repository

    def _repo(self) -> KnowledgeRepository:
        if self._repository is None:
            raise ConflictError("Reviewed knowledge persistence is unavailable for this client")
        return self._repository

    def create_draft(
        self,
        collection_id: str | uuid.UUID,
        graph: dict[str, Any],
        *,
        evidence_ids: list[str | uuid.UUID] | tuple[str | uuid.UUID, ...] = (),
        actor: str,
        idempotency_key: str | None = None,
    ) -> tuple[DraftGraph, DraftRevision]:
        if not isinstance(graph, dict):
            raise ValidationError("graph must be an object")
        return self._repo().create_draft(
            collection_id=_identifier(collection_id, "collection_id"),
            graph=graph,
            evidence_ids=tuple(_identifier(value, "evidence_id") for value in evidence_ids),
            actor=_required_text(actor, "actor"),
            idempotency_key=idempotency_key,
        )

    def create_correction_request(
        self,
        target_fact_revision_id: str | uuid.UUID,
        correction_context: dict[str, Any],
        *,
        actor: str,
        idempotency_key: str | None = None,
    ) -> tuple[DraftGraph, DraftRevision]:
        if not isinstance(correction_context, dict):
            raise ValidationError("correction_context must be an object")
        return self._repo().create_correction_request(
            target_fact_revision_id=_identifier(
                target_fact_revision_id, "target_fact_revision_id"
            ),
            correction_context=correction_context,
            actor=_required_text(actor, "actor"),
            idempotency_key=idempotency_key,
        )

    def get_draft(self, draft_id: str | uuid.UUID) -> DraftGraph | None:
        return self._repo().get_draft(_identifier(draft_id, "draft_id"))

    def list_drafts(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DraftGraph]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValidationError("limit must be between 1 and 1000 and offset non-negative")
        normalized_status = status.strip().upper() if status else None
        if status is not None and not normalized_status:
            raise ValidationError("status must not be empty")
        identifier = _identifier(collection_id, "collection_id") if collection_id else None
        return self._repo().list_drafts(
            collection_id=identifier,
            status=normalized_status,
            limit=limit,
            offset=offset,
        )

    def get_revision(
        self, draft_id: str | uuid.UUID, revision: int | None = None
    ) -> DraftRevision | None:
        if revision is not None and revision < 1:
            raise ValidationError("revision must be positive")
        return self._repo().get_revision(_identifier(draft_id, "draft_id"), revision)

    def revise(
        self,
        draft_id: str | uuid.UUID,
        *,
        expected_revision: int,
        graph: dict[str, Any],
        evidence_ids: list[str | uuid.UUID] | tuple[str | uuid.UUID, ...] = (),
        actor: str,
        change_note: str | None = None,
        idempotency_key: str | None = None,
    ) -> DraftRevision:
        if expected_revision < 1:
            raise ValidationError("expected_revision must be positive")
        if not isinstance(graph, dict):
            raise ValidationError("graph must be an object")
        return self._repo().revise(
            draft_id=_identifier(draft_id, "draft_id"),
            expected_revision=expected_revision,
            graph=graph,
            evidence_ids=tuple(_identifier(value, "evidence_id") for value in evidence_ids),
            actor=_required_text(actor, "actor"),
            change_note=change_note,
            idempotency_key=idempotency_key,
        )

    def submit_review(
        self,
        draft_id: str | uuid.UUID,
        *,
        expected_revision: int,
        actor: str,
        notes: str | None = None,
        idempotency_key: str | None = None,
    ) -> ReviewDecision:
        if expected_revision < 1:
            raise ValidationError("expected_revision must be positive")
        return self._repo().submit_review(
            draft_id=_identifier(draft_id, "draft_id"),
            expected_revision=expected_revision,
            actor=_required_text(actor, "actor"),
            notes=notes,
            idempotency_key=idempotency_key,
        )

    def approve(
        self,
        draft_id: str | uuid.UUID,
        *,
        expected_revision: int,
        actor: str,
        notes: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[ReviewDecision, FactRevision, OutboxEvent]:
        if expected_revision < 1:
            raise ValidationError("expected_revision must be positive")
        return self._repo().approve(
            draft_id=_identifier(draft_id, "draft_id"),
            expected_revision=expected_revision,
            actor=_required_text(actor, "actor"),
            notes=notes,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def reject(
        self,
        draft_id: str | uuid.UUID,
        *,
        expected_revision: int,
        actor: str,
        notes: str | None = None,
        idempotency_key: str | None = None,
    ) -> ReviewDecision:
        if expected_revision < 1:
            raise ValidationError("expected_revision must be positive")
        return self._repo().reject(
            draft_id=_identifier(draft_id, "draft_id"),
            expected_revision=expected_revision,
            actor=_required_text(actor, "actor"),
            notes=notes,
            idempotency_key=idempotency_key,
        )

    def get_fact(self, fact_id: str | uuid.UUID) -> FactRevision | None:
        return self._repo().get_fact(_identifier(fact_id, "fact_id"))

    def list_facts(
        self,
        *,
        collection_id: str | uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FactRevision]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValidationError("limit must be between 1 and 1000 and offset non-negative")
        identifier = _identifier(collection_id, "collection_id") if collection_id else None
        return self._repo().list_facts(collection_id=identifier, limit=limit, offset=offset)

    def list_events(self, *, limit: int = 100, offset: int = 0) -> list[OutboxEvent]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValidationError("limit must be between 1 and 1000 and offset non-negative")
        return self._repo().list_events(limit=limit, offset=offset)
