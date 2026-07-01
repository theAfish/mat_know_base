"""Persistence tool for the raw workflow extraction agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pydantic import ValidationError

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import RawWorkflowExtraction
from mkb.workflows.contract import RawWorkflowGraph
from mkb.workflows.review import audit_raw_graph
from mkb.workflows.validation import compact_validation_errors
from mkb.workflows.editing import (
    compact_raw_checkpoint_manifest as _compact_raw_checkpoint_manifest,
    get_active_workflow_card_library,
    normalize_raw_graph_payload as _normalize_raw_graph_payload,
    search_workflow_cards,
)

def get_raw_workflow_checkpoint(extraction_id: str) -> dict:
    """Read the latest saved checkpoint for an unfinished raw workflow."""
    try:
        eid = uuid.UUID(extraction_id)
    except (TypeError, ValueError, AttributeError):
        return {"error": f"Invalid extraction_id: {extraction_id}"}

    with SyncSessionLocal() as session:
        row = session.query(RawWorkflowExtraction).filter_by(extraction_id=eid).first()
        if not row:
            return {"error": f"Extraction {eid} not found"}
        checkpoint = row.checkpoint or {}
        draft = checkpoint.get("graph") if isinstance(checkpoint, dict) else None
        return {
            "extraction_id": str(eid),
            "project_id": str(row.project_id),
            "version": row.version,
            "status": row.status,
            "checkpoint": {
                "summary": checkpoint.get("summary") if isinstance(checkpoint, dict) else None,
                "manifest": _compact_raw_checkpoint_manifest(draft),
            },
            "checkpoint_updated_at": (
                row.checkpoint_updated_at.isoformat() if row.checkpoint_updated_at else None
            ),
        }


def checkpoint_raw_workflow(
    extraction_id: str,
    summary: str,
    graph: dict | None = None,
) -> dict:
    """Persist a resumable checkpoint for an unfinished raw workflow."""
    try:
        eid = uuid.UUID(extraction_id)
    except (TypeError, ValueError, AttributeError):
        return {"error": f"Invalid extraction_id: {extraction_id}"}

    if not isinstance(summary, str) or not summary.strip():
        return {"error": "summary is required"}
    if graph is not None and not isinstance(graph, dict):
        return {"error": "graph must be a JSON object when provided"}

    with SyncSessionLocal() as session:
        row = session.query(RawWorkflowExtraction).filter_by(extraction_id=eid).first()
        if not row:
            return {"error": f"Extraction {eid} not found"}
        if row.graph is not None or row.status not in {"IN_PROGRESS", "FAILED"}:
            return {"error": "This extraction can no longer accept checkpoints"}

        previous = row.checkpoint or {}
        checkpoint_count = int((row.provenance or {}).get("checkpoint_count", 0)) + 1
        row.checkpoint = {
            "summary": summary.strip(),
            "graph": graph if graph is not None else previous.get("graph"),
        }
        row.checkpoint_updated_at = datetime.now(timezone.utc)
        row.provenance = {
            **(row.provenance or {}),
            "checkpoint_count": checkpoint_count,
            "last_checkpoint_summary": summary.strip()[:500],
        }
        session.commit()
        return {
            "status": "checkpointed",
            "extraction_id": str(eid),
            "version": row.version,
            "has_graph": row.checkpoint.get("graph") is not None,
            "checkpoint_count": checkpoint_count,
        }


def save_raw_workflow(extraction_id: str, graph: dict) -> dict:
    """Validate and immutably save one raw workflow extraction graph."""
    try:
        eid = uuid.UUID(extraction_id)
    except (TypeError, ValueError, AttributeError):
        return {"error": f"Invalid extraction_id: {extraction_id}"}

    with SyncSessionLocal() as session:
        row = session.query(RawWorkflowExtraction).filter_by(extraction_id=eid).first()
        if not row:
            return {"error": f"Extraction {eid} not found"}
        if row.status != "IN_PROGRESS" or row.graph is not None:
            return {"error": "This extraction version has already been finalized"}

        payload, normalization = _normalize_raw_graph_payload(graph, row, eid)
        try:
            validated = RawWorkflowGraph.model_validate(payload)
        except ValidationError as exc:
            return {
                "error": "Raw workflow contract validation failed",
                "details": compact_validation_errors(exc),
                "normalization": normalization,
                "hint": (
                    "The tool fills schema_version, paper_id, extraction_id, sequential IDs, "
                    "default dict/list fields, and deterministic edge relations from endpoint "
                    "node kinds. If validation still fails, fix the listed endpoints or node "
                    "kinds rather than resending the full source content."
                ),
            }

        payload = validated.model_dump(mode="json")
        asset_ids = sorted({
            node.paper_location.asset_id
            for node in validated.nodes
            if node.paper_location.asset_id
        })
        row.graph = payload
        row.checkpoint = None
        row.checkpoint_updated_at = datetime.now(timezone.utc)
        row.status = "COMPLETED"
        row.review_flags = audit_raw_graph(payload)
        if row.review_flags:
            row.record_status = "needs_review"
        previous = (
            session.query(RawWorkflowExtraction)
            .filter(
                RawWorkflowExtraction.project_id == row.project_id,
                RawWorkflowExtraction.extraction_id != row.extraction_id,
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
            )
            .order_by(RawWorkflowExtraction.version.desc())
            .first()
        )
        if previous:
            previous.record_status = "superseded"
            row.supersedes_extraction_id = previous.extraction_id
            previous.review_flags = audit_raw_graph(previous.graph or {}, later_graph=payload)
        row.extracted_at = datetime.now(timezone.utc)
        row.provenance = {**(row.provenance or {}), "source_asset_ids": asset_ids}
        session.commit()
        return {
            "status": "completed",
            "extraction_id": str(eid),
            "version": row.version,
            "node_count": len(validated.nodes),
            "edge_count": len(validated.edges),
            "normalization": normalization,
        }


WORKFLOW_EXTRACTION_TOOLS = [
    get_active_workflow_card_library,
    search_workflow_cards,
    get_raw_workflow_checkpoint,
    checkpoint_raw_workflow,
    save_raw_workflow,
]
