"""Grounded context and persistence tools for the Schema Curator Agent."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from sqlalchemy import func

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    CanonicalWorkflow, RawWorkflowExtraction, SchemaProposal,
    SchemaProposalRevision, WorkflowSchemaVersion,
)
from mkb.workflows.curator import analyze_canonical_workflows, validate_proposal
from mkb.workflows.schema_library import get_schema_library_payload

_CURATOR_AUTHOR: ContextVar[str] = ContextVar(
    "schema_curator_author", default="schema-curator-agent/unknown-model"
)


def set_curator_author(author: str):
    return _CURATOR_AUTHOR.set(author)


def reset_curator_author(token) -> None:
    _CURATOR_AUTHOR.reset(token)


def _workflow_evidence(row: CanonicalWorkflow, raw: RawWorkflowExtraction | None) -> dict:
    graph = row.graph or {}
    raw_graph = raw.graph if raw and raw.graph else {}
    raw_nodes = {node.get("node_id"): node for node in raw_graph.get("nodes", [])}
    unmatched = []
    for item in graph.get("unmatched_raw_information", []):
        for raw_id in item.get("raw_node_ids", []):
            node = raw_nodes.get(raw_id, {})
            unmatched.append({
                "raw_node_id": raw_id,
                "raw_name": node.get("raw_name"),
                "node_kind": node.get("node_kind_guess"),
                "evidence_text": str(node.get("evidence_text") or "")[:600],
                "reason": item.get("reason"),
            })
    return {
        "canonicalization_id": str(row.canonicalization_id),
        "project_id": str(row.project_id),
        "schema_version": row.schema_version,
        "nodes": [{
            "node_id": node.get("node_id"),
            "label": node.get("label"),
            "node_kind": node.get("node_kind"),
            "object_schema": node.get("object_schema"),
            "operation_template_id": node.get("operation_template_id"),
            "attributes": node.get("attributes", {}),
        } for node in graph.get("nodes", [])[:60]],
        "unmatched": unmatched[:40],
        "granularity_mappings": graph.get("granularity_mappings", []),
        "existing_schema_suggestions": graph.get("proposed_schema_updates", [])[:20],
    }


def _card_workflow_evidence(row: RawWorkflowExtraction) -> dict:
    """Bounded evidence view for a v2 extraction with no canonicalization pass."""
    graph = row.graph or {}
    return {
        "workflow_id": str(row.extraction_id),
        # compatibility key used by the proposal UI/storage layer
        "canonicalization_id": str(row.extraction_id),
        "project_id": str(row.project_id),
        "schema_version": row.schema_version,
        "nodes": [{
            "node_id": node.get("node_id"),
            "label": node.get("canonical_name") or node.get("raw_name"),
            "raw_name": node.get("raw_name"),
            "node_kind": node.get("node_kind") or node.get("node_kind_guess"),
            "semantic_type": node.get("semantic_type"),
            "card_id": node.get("card_id"),
            "ontology_status": node.get("ontology_status"),
            "parameters": node.get("parameters", {}),
            "identity": node.get("identity", {}),
            "state": node.get("state", {}),
            "role": node.get("role", {}),
            "context": node.get("context", {}),
            "evidence_text": str(node.get("evidence_text") or "")[:600],
        } for node in graph.get("nodes", [])[:80]],
        "edges": graph.get("edges", [])[:120],
        "reproducibility": graph.get("reproducibility", {}),
        "unresolved_information": graph.get("unresolved_information", [])[:30],
    }


def _as_induction_workflow(row: RawWorkflowExtraction) -> dict:
    """Adapt card instances to the deterministic discovery signal analyzer."""
    raw_graph = row.graph or {}
    nodes = []
    unmatched = []
    for node in raw_graph.get("nodes", []):
        kind = node.get("node_kind") or node.get("node_kind_guess")
        card_id = node.get("card_id")
        nodes.append({
            "node_id": node.get("node_id"),
            "label": node.get("canonical_name") or node.get("raw_name"),
            "node_kind": kind,
            "object_schema": node.get("semantic_type") if kind == "object" else None,
            "operation_template_id": card_id if kind == "operation" else None,
            "attributes": node.get("parameters", {}),
        })
        if kind == "operation" and not card_id:
            unmatched.append({"raw_node_ids": [node.get("node_id")], "reason": "unmapped card"})
    return {
        "canonicalization_id": str(row.extraction_id),
        "graph": {
            "nodes": nodes,
            "edges": raw_graph.get("edges", []),
            "unmatched_raw_information": unmatched,
            "granularity_mappings": [
                {"coarse": edge.get("source_node"), "fine": edge.get("target_node")}
                for edge in raw_graph.get("edges", [])
                if edge.get("relation_type") in {"part_of", "has_part", "expands_to", "summarized_by"}
            ],
        },
        "raw_graph": raw_graph,
    }


def get_schema_curator_context(min_support: int = 2, max_workflows: int = 40) -> dict:
    """Load global schema, deterministic discovery signals, and bounded evidence."""
    min_support = max(1, int(min_support))
    max_workflows = min(80, max(1, int(max_workflows)))
    with SyncSessionLocal() as session:
        active = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        library = active.payload if active else get_schema_library_payload()
        requested_revisions = session.query(SchemaProposal).filter_by(
            status="revision_requested"
        ).order_by(SchemaProposal.created_at).all()
        rows = session.query(CanonicalWorkflow).filter_by(status="COMPLETED").order_by(
            CanonicalWorkflow.created_at.desc()
        ).limit(max_workflows).all()
        included_ids = {str(row.canonicalization_id) for row in rows}
        required_ids = {
            evidence_id
            for proposal in requested_revisions
            for evidence_id in (proposal.evidence_workflow_ids or [])
            if evidence_id not in included_ids
        }
        if required_ids:
            rows.extend(session.query(CanonicalWorkflow).filter(
                CanonicalWorkflow.status == "COMPLETED",
                CanonicalWorkflow.canonicalization_id.in_([
                    uuid.UUID(value) for value in required_ids
                ]),
            ).all())
        workflows = []
        evidence = []
        for row in rows:
            raw = session.query(RawWorkflowExtraction).filter_by(
                extraction_id=row.raw_extraction_id
            ).first()
            workflows.append({
                "canonicalization_id": str(row.canonicalization_id),
                "graph": row.graph,
                "raw_graph": raw.graph if raw else {},
            })
            evidence.append(_workflow_evidence(row, raw))
        # V2 graphs are already structured card instances. Include their latest
        # active versions directly and avoid requiring the removed per-paper
        # canonicalization stage. Old canonical workflows remain readable while
        # installations migrate.
        card_rows = (
            session.query(RawWorkflowExtraction)
            .filter(
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.schema_version == "workflow-cards/2.0",
                RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
            )
            .order_by(RawWorkflowExtraction.created_at.desc())
            .limit(max_workflows)
            .all()
        )
        card_workflows = []
        card_evidence = []
        for row in card_rows:
            card_workflows.append(_as_induction_workflow(row))
            card_evidence.append(_card_workflow_evidence(row))
        # Prefer the new direct evidence layer; fill any remaining context
        # budget with legacy canonicalizations.
        workflows = (card_workflows + workflows)[:max_workflows]
        evidence = (card_evidence + evidence)[:max_workflows]
        candidates = analyze_canonical_workflows(workflows, min_support=min_support)
        diagnostics = (
            candidates[0].get("analysis", {}).get("diagnostics", {})
            if candidates else {}
        )
        for candidate in candidates:
            candidate.get("analysis", {}).pop("diagnostics", None)
        return {
            "schema_library": library,
            "workflow_count": len(workflows),
            "min_support": min_support,
            "deterministic_candidates": candidates,
            "corpus_diagnostics": diagnostics,
            "workflow_evidence": evidence,
            "revision_requested_proposals": [{
                "proposal_id": str(proposal.proposal_id),
                "proposal_type": proposal.proposal_type,
                "payload": proposal.payload,
                "evidence_workflow_ids": proposal.evidence_workflow_ids,
                "rationale": proposal.rationale,
                "reviewer_notes": proposal.reviewer_notes,
                "base_schema_version": proposal.base_schema_version,
            } for proposal in requested_revisions],
            "instructions": (
                "Use only workflow_id/canonicalization_id values from workflow_evidence as evidence. "
                "Candidates are signals that require semantic review."
            ),
        }


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
    author = _CURATOR_AUTHOR.get()
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
    author = _CURATOR_AUTHOR.get()
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


SCHEMA_CURATOR_TOOLS = [
    get_schema_curator_context, submit_schema_proposal, revise_schema_proposal,
]
