"""Transactional schema-curator mutations."""

from __future__ import annotations

import uuid
from sqlalchemy import func

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    CanonicalWorkflow,
    RawWorkflowExtraction,
    SchemaProposal,
    SchemaProposalRevision,
    WorkflowSchemaVersion,
)
from mkb.services.agent_tools.curator_state import CURATOR_AUTHOR
from mkb.workflows.curator import validate_proposal
from mkb.workflows.review import audit_raw_graph, rebase_graph
from mkb.workflows.schema_library import get_schema_library_payload

def submit_schema_proposal(
    proposal_type: str,
    payload: dict,
    evidence_workflow_ids: list[str],
    rationale: str,
    analysis: dict | None = None,
) -> dict:
    """Validate and save one LLM-authored proposal for human review."""
    if not rationale.strip():
        return {"error": "rationale is required"}
    try:
        evidence_uuids = [uuid.UUID(value) for value in evidence_workflow_ids]
    except (TypeError, ValueError, AttributeError):
        return {"error": "evidence_workflow_ids must contain canonicalization UUIDs"}
    author = CURATOR_AUTHOR.get()
    with SyncSessionLocal() as session:
        active = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        library = active.payload if active else get_schema_library_payload()
        base_version = active.name if active else library["schema_version"]
        known = {
            str(value) for (value,) in session.query(CanonicalWorkflow.canonicalization_id).filter(
                CanonicalWorkflow.status == "COMPLETED",
                CanonicalWorkflow.canonicalization_id.in_(evidence_uuids),
            ).all()
        }
        known.update({
            str(value) for (value,) in session.query(RawWorkflowExtraction.extraction_id).filter(
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.extraction_id.in_(evidence_uuids),
            ).all()
        })
        errors = validate_proposal(
            proposal_type, payload, evidence_workflow_ids, library,
        )
        missing = sorted(set(evidence_workflow_ids) - known)
        if missing:
            errors.append(f"unknown evidence workflows: {', '.join(missing)}")
        if errors:
            return {"error": "proposal validation failed", "details": errors}
        duplicate = session.query(SchemaProposal).filter(
            SchemaProposal.status.in_(("pending", "revision_requested")),
            SchemaProposal.proposal_type == proposal_type,
            SchemaProposal.payload == payload,
        ).first()
        if duplicate:
            return {
                "status": "duplicate", "proposal_id": str(duplicate.proposal_id),
            }
        proposal = SchemaProposal(
            proposal_type=proposal_type, status="pending", payload=payload,
            evidence_workflow_ids=evidence_workflow_ids,
            analysis={**(analysis or {}), "curator_method": "llm", "validation_errors": []},
            rationale=rationale.strip(), base_schema_version=base_version,
            created_by=author,
        )
        session.add(proposal)
        session.flush()
        session.add(SchemaProposalRevision(
            proposal_id=proposal.proposal_id, revision_number=1,
            payload=proposal.payload,
            evidence_workflow_ids=proposal.evidence_workflow_ids,
            analysis=proposal.analysis, rationale=proposal.rationale,
            author=author, author_type="agent",
            change_note="Initial LLM curator draft", validation_errors=[],
        ))
        session.commit()
        return {
            "status": "created", "proposal_id": str(proposal.proposal_id),
            "base_schema_version": base_version,
        }


def revise_schema_proposal(
    proposal_id: str,
    payload: dict,
    evidence_workflow_ids: list[str],
    rationale: str,
    response_to_review: str,
) -> dict:
    """Respond to human revision notes with a validated agent-authored revision."""
    try:
        pid = uuid.UUID(proposal_id)
        evidence_uuids = [uuid.UUID(value) for value in evidence_workflow_ids]
    except (TypeError, ValueError, AttributeError):
        return {"error": "proposal and evidence IDs must be UUIDs"}
    if not rationale.strip() or not response_to_review.strip():
        return {"error": "rationale and response_to_review are required"}
    author = CURATOR_AUTHOR.get()
    with SyncSessionLocal() as session:
        proposal = session.query(SchemaProposal).filter_by(proposal_id=pid).first()
        if not proposal or proposal.status != "revision_requested":
            return {"error": "Revision-requested proposal not found"}
        active = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        library = active.payload if active else get_schema_library_payload()
        base_version = active.name if active else library["schema_version"]
        known = {
            str(value) for (value,) in session.query(CanonicalWorkflow.canonicalization_id).filter(
                CanonicalWorkflow.status == "COMPLETED",
                CanonicalWorkflow.canonicalization_id.in_(evidence_uuids),
            ).all()
        }
        known.update({
            str(value) for (value,) in session.query(RawWorkflowExtraction.extraction_id).filter(
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.extraction_id.in_(evidence_uuids),
            ).all()
        })
        errors = validate_proposal(
            proposal.proposal_type, payload, evidence_workflow_ids, library,
        )
        missing = sorted(set(evidence_workflow_ids) - known)
        if missing:
            errors.append(f"unknown evidence workflows: {', '.join(missing)}")
        if errors:
            return {"error": "revised proposal validation failed", "details": errors}
        revision_number = int(
            session.query(func.coalesce(func.max(SchemaProposalRevision.revision_number), 0))
            .filter_by(proposal_id=pid).scalar()
        ) + 1
        proposal.payload = payload
        proposal.evidence_workflow_ids = evidence_workflow_ids
        proposal.rationale = rationale.strip()
        proposal.base_schema_version = base_version
        proposal.analysis = {
            **(proposal.analysis or {}), "curator_method": "llm",
            "revision_response": response_to_review.strip(),
            "validation_errors": [],
        }
        proposal.status = "pending"
        session.add(SchemaProposalRevision(
            proposal_id=pid, revision_number=revision_number,
            payload=payload, evidence_workflow_ids=evidence_workflow_ids,
            analysis=proposal.analysis, rationale=proposal.rationale,
            author=author, author_type="agent",
            change_note=f"Agent response to review: {response_to_review.strip()}",
            validation_errors=[],
        ))
        session.commit()
        return {
            "status": "revised", "proposal_id": proposal_id,
            "revision_number": revision_number,
        }


def submit_workflow_review(
    workflow_id: str,
    graph: dict,
    reason: str,
    evidence: str,
    affected_nodes: list[str] | None = None,
    affected_edges: list[str] | None = None,
) -> dict:
    """Persist an immutable reviewed workflow revision with edited nodes/edges."""
    if not reason.strip() or not evidence.strip():
        return {"error": "reason and evidence are required"}
    try:
        wid = uuid.UUID(str(workflow_id))
    except (TypeError, ValueError, AttributeError):
        return {"error": "workflow_id must be a UUID"}
    author = CURATOR_AUTHOR.get()
    with SyncSessionLocal() as session:
        source = session.query(RawWorkflowExtraction).filter_by(extraction_id=wid).first()
        if not source or source.status != "COMPLETED":
            return {"error": "completed workflow not found"}
        new_id = uuid.uuid4()
        corrected = dict(graph or {})
        corrected["paper_id"] = str(source.project_id)
        corrected["schema_version"] = source.schema_version
        try:
            corrected = rebase_graph(corrected, new_id)
        except Exception as exc:
            message = " ".join(str(exc).split())
            return {
                "error": "corrected graph validation failed",
                "details": message[:1000],
            }
        version = int(
            session.query(func.coalesce(func.max(RawWorkflowExtraction.version), 0))
            .filter_by(project_id=source.project_id)
            .scalar()
        ) + 1
        review_count = int((source.provenance or {}).get("workflow_review_count", 0)) + 1
        flags = audit_raw_graph(corrected)
        row = RawWorkflowExtraction(
            extraction_id=new_id,
            project_id=source.project_id,
            version=version,
            schema_version=source.schema_version,
            extractor_version=source.extractor_version,
            model=source.model,
            status="COMPLETED",
            record_status="needs_review" if flags else "active",
            supersedes_extraction_id=source.extraction_id,
            graph=corrected,
            correction_reason=reason.strip(),
            correction_author=author,
            correction_details={
                "affected_nodes": affected_nodes or [],
                "affected_edges": affected_edges or [],
                "evidence": evidence.strip(),
            },
            review_flags=flags,
            provenance={
                **(source.provenance or {}),
                "workflow_review_count": review_count,
                "last_review_agent": author,
                "last_review_reason": reason.strip(),
            },
            extracted_at=source.extracted_at,
        )
        source.record_status = "superseded"
        session.add(row)
        session.commit()
        return {
            "status": "reviewed",
            "workflow_id": str(new_id),
            "project_id": str(source.project_id),
            "version": version,
            "review_flags": flags,
        }


