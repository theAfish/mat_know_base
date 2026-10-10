"""Versioned integration API for external agent harnesses and services."""

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from mkb.exceptions import ConflictError, NotFoundError, ValidationError
from mkb.web.contracts_v1 import ApiCapability, ApiDiscovery, ProvenanceReference
from mkb.web.dependencies import get_knowledge_base

router = APIRouter(prefix="/api/v1", tags=["integration-v1"])


class DraftWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: dict[str, Any]
    evidence_ids: list[str] = Field(default_factory=list)
    change_note: str | None = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    notes: str | None = None


class DraftRevisionRequest(DraftWriteRequest):
    expected_revision: int = Field(ge=1)


class CorrectionIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_event_id: str = Field(min_length=1)
    source_correction_id: str = Field(min_length=1)
    target: ProvenanceReference
    issue_summary: str = Field(min_length=1)
    proposed_correction: str | None = Field(default=None, min_length=1)
    evidence: list[ProvenanceReference] = Field(min_length=1)


def _actor(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return getattr(principal, "actor", "local-user")


def _translate_error(exc: Exception) -> HTTPException:
    status = 404 if isinstance(exc, NotFoundError) else 409 if isinstance(exc, ConflictError) else 400
    return HTTPException(status_code=status, detail=str(exc))


@router.get("", response_model=ApiDiscovery)
def discover_api_v1() -> ApiDiscovery:
    return ApiDiscovery(
        capabilities=(
            ApiCapability(name="discovery", path="/api/v1", status="available"),
            ApiCapability(name="drafts", path="/api/v1/drafts", status="available"),
            ApiCapability(name="facts", path="/api/v1/facts", status="available"),
            ApiCapability(name="events", path="/api/v1/integration-events", status="available"),
            ApiCapability(
                name="correction_intake",
                path="/api/v1/correction-requests",
                status="available",
            ),
        )
    )


@router.post("/drafts", status_code=201)
def create_draft(
    collection_id: str,
    body: DraftWriteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        draft, revision = get_knowledge_base().knowledge.create_draft(
            collection_id,
            body.graph,
            evidence_ids=body.evidence_ids,
            actor=_actor(request),
            idempotency_key=idempotency_key,
        )
        return {"draft": draft, "revision": revision}
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/correction-requests", status_code=201)
def ingest_correction_request(
    body: CorrectionIntakeRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if body.target.service != "mkb" or body.target.resource_type != "fact_revision":
        raise HTTPException(
            status_code=400,
            detail="Correction target must be an MKB fact_revision",
        )
    context = {
        "source_service": "kdg",
        "source_event_id": body.source_event_id,
        "source_correction_id": body.source_correction_id,
        "target": body.target.model_dump(mode="json"),
        "issue_summary": body.issue_summary,
        "proposed_correction": body.proposed_correction,
        "evidence": [item.model_dump(mode="json") for item in body.evidence],
        "correlation_id": request.headers.get("X-Request-ID"),
        "causation_id": request.headers.get("X-Causation-ID"),
    }
    try:
        draft, revision = get_knowledge_base().knowledge.create_correction_request(
            body.target.resource_id,
            context,
            actor=_actor(request),
            idempotency_key=idempotency_key or f"kdg:{body.source_event_id}",
        )
        return {"draft": draft, "revision": revision}
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get("/drafts")
def list_drafts(
    collection_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    try:
        return get_knowledge_base().knowledge.list_drafts(
            collection_id=collection_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str):
    try:
        draft = get_knowledge_base().knowledge.get_draft(draft_id)
        if draft is None:
            raise NotFoundError(f"Draft not found: {draft_id}")
        revision = get_knowledge_base().knowledge.get_revision(draft_id)
        return {"draft": draft, "revision": revision}
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/drafts/{draft_id}/revisions", status_code=201)
def revise_draft(
    draft_id: str,
    body: DraftRevisionRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return get_knowledge_base().knowledge.revise(
            draft_id,
            expected_revision=body.expected_revision,
            graph=body.graph,
            evidence_ids=body.evidence_ids,
            actor=_actor(request),
            change_note=body.change_note,
            idempotency_key=idempotency_key,
        )
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/drafts/{draft_id}/submit-review")
def submit_review(
    draft_id: str,
    body: ReviewRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return get_knowledge_base().knowledge.submit_review(
            draft_id,
            expected_revision=body.expected_revision,
            actor=_actor(request),
            notes=body.notes,
            idempotency_key=idempotency_key,
        )
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/drafts/{draft_id}/approve")
def approve_draft(
    draft_id: str,
    body: ReviewRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        decision, fact, event = get_knowledge_base().knowledge.approve(
            draft_id,
            expected_revision=body.expected_revision,
            actor=_actor(request),
            notes=body.notes,
            idempotency_key=idempotency_key,
            correlation_id=request.headers.get("X-Request-ID"),
        )
        return {"decision": decision, "fact": fact, "event_id": event.id}
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.post("/drafts/{draft_id}/reject")
def reject_draft(
    draft_id: str,
    body: ReviewRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return get_knowledge_base().knowledge.reject(
            draft_id,
            expected_revision=body.expected_revision,
            actor=_actor(request),
            notes=body.notes,
            idempotency_key=idempotency_key,
        )
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get("/facts")
def list_facts(collection_id: str | None = None, limit: int = 100, offset: int = 0):
    try:
        return get_knowledge_base().knowledge.list_facts(
            collection_id=collection_id, limit=limit, offset=offset
        )
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get("/facts/{fact_id}")
def get_fact(fact_id: str):
    try:
        fact = get_knowledge_base().knowledge.get_fact(fact_id)
        if fact is None:
            raise NotFoundError(f"Fact revision not found: {fact_id}")
        return fact
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc


@router.get("/integration-events")
def list_integration_events(limit: int = 100, offset: int = 0):
    try:
        return get_knowledge_base().knowledge.list_events(limit=limit, offset=offset)
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _translate_error(exc) from exc
