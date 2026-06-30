"""Persistence tool for the raw workflow extraction agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import RawWorkflowExtraction
from mkb.workflows.contract import RawWorkflowGraph
from mkb.workflows.schema_library import get_schema_library_payload
from mkb.workflows.review import audit_raw_graph
from mkb.workflows.validation import json_safe_validation_errors


def get_active_workflow_card_library(
    max_cards: int = 40,
    max_templates: int = 40,
) -> dict:
    """Return a bounded view of the newest workflow card/schema library."""
    library = get_schema_library_payload()
    cards = library.get("cards", {})
    templates = library.get("operation_templates", {})
    card_items = list(cards.items())[: max(1, min(int(max_cards), 200))]
    template_items = list(templates.items())[: max(1, min(int(max_templates), 200))]
    return {
        "schema_version": library.get("schema_version"),
        "cards": [
            {
                "card_id": card_id,
                "canonical_name": payload.get("canonical_name"),
                "kind": payload.get("kind"),
                "aliases": payload.get("aliases", []),
                "parameter_slots": payload.get("parameter_slots", []),
                "status": payload.get("status", "active"),
                "replaced_by": payload.get("replaced_by"),
            }
            for card_id, payload in card_items
        ],
        "operation_templates": [
            {
                "template_id": template_id,
                "label": payload.get("label"),
                "aliases": payload.get("aliases", []),
                "slots": payload.get("slots", []),
                "parameters": payload.get("parameters", {}),
                "deprecated": bool(payload.get("deprecated")),
            }
            for template_id, payload in template_items
        ],
    }


def search_workflow_cards(
    query: str,
    node_kind: str | None = None,
    limit: int = 10,
) -> dict:
    """Search the newest card base/templates before instantiating workflow nodes."""
    text = str(query or "").strip().casefold()
    if not text:
        return {"error": "query is required"}
    if node_kind not in {None, "", "object", "operation"}:
        return {"error": "node_kind must be 'object', 'operation', or omitted"}

    library = get_schema_library_payload()
    results: list[dict] = []
    effective_limit = max(1, min(int(limit), 50))

    for card_id, payload in (library.get("cards", {}) or {}).items():
        kind = payload.get("kind")
        if node_kind and kind != node_kind:
            continue
        name = str(payload.get("canonical_name") or "")
        aliases = [str(value) for value in payload.get("aliases", []) if value]
        haystacks = [card_id, name, *aliases]
        score = sum(3 for item in haystacks if text == item.casefold())
        score += sum(1 for item in haystacks if text in item.casefold())
        if score <= 0:
            continue
        results.append({
            "match_type": "card",
            "score": score,
            "card_id": card_id,
            "canonical_name": name,
            "kind": kind,
            "aliases": aliases,
            "parameter_slots": payload.get("parameter_slots", []),
            "status": payload.get("status", "active"),
            "replaced_by": payload.get("replaced_by"),
        })

    for template_id, payload in (library.get("operation_templates", {}) or {}).items():
        if node_kind == "object":
            continue
        label = str(payload.get("label") or "")
        aliases = [str(value) for value in payload.get("aliases", []) if value]
        haystacks = [template_id, label, *aliases]
        score = sum(3 for item in haystacks if text == item.casefold())
        score += sum(1 for item in haystacks if text in item.casefold())
        if score <= 0:
            continue
        results.append({
            "match_type": "operation_template",
            "score": score,
            "template_id": template_id,
            "label": label,
            "kind": "operation",
            "aliases": aliases,
            "slots": payload.get("slots", []),
            "parameters": payload.get("parameters", {}),
            "deprecated": bool(payload.get("deprecated")),
        })

    results.sort(
        key=lambda item: (
            -int(item.get("score", 0)),
            str(item.get("canonical_name") or item.get("label") or item.get("card_id") or item.get("template_id")),
        )
    )
    return {
        "schema_version": library.get("schema_version"),
        "query": query,
        "node_kind": node_kind or "any",
        "results": results[:effective_limit],
    }


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
        return {
            "extraction_id": str(eid),
            "project_id": str(row.project_id),
            "version": row.version,
            "status": row.status,
            "checkpoint": row.checkpoint,
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

    try:
        validated = RawWorkflowGraph.model_validate(graph)
    except ValidationError as exc:
        return {
            "error": "Raw workflow contract validation failed",
            "details": json_safe_validation_errors(exc),
        }

    if validated.extraction_id != str(eid):
        return {"error": "graph.extraction_id does not match extraction_id"}

    expected_node_prefix = f"raw:{eid}:n"
    expected_edge_prefix = f"raw:{eid}:e"
    if any(not node.node_id.startswith(expected_node_prefix) for node in validated.nodes):
        return {"error": f"All node IDs must start with {expected_node_prefix}"}
    if any(not edge.edge_id.startswith(expected_edge_prefix) for edge in validated.edges):
        return {"error": f"All edge IDs must start with {expected_edge_prefix}"}

    with SyncSessionLocal() as session:
        row = session.query(RawWorkflowExtraction).filter_by(extraction_id=eid).first()
        if not row:
            return {"error": f"Extraction {eid} not found"}
        if row.status != "IN_PROGRESS" or row.graph is not None:
            return {"error": "This extraction version has already been finalized"}
        if validated.paper_id != str(row.project_id):
            return {"error": "graph.paper_id does not match the extraction project"}

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
        }


WORKFLOW_EXTRACTION_TOOLS = [
    get_active_workflow_card_library,
    search_workflow_cards,
    get_raw_workflow_checkpoint,
    checkpoint_raw_workflow,
    save_raw_workflow,
]
