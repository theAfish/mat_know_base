"""Context and persistence tools for canonical workflow generation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction
from mkb.workflows.canonical_contract import CanonicalWorkflowGraph
from mkb.workflows.schema_library import get_schema_library_payload


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        return None


def get_canonicalization_context(canonicalization_id: str) -> dict:
    """Load the raw graph and current schema library for a pending run."""
    cid = _uuid(canonicalization_id)
    if not cid:
        return {"error": "Invalid canonicalization_id"}
    with SyncSessionLocal() as session:
        row = session.query(CanonicalWorkflow).filter_by(canonicalization_id=cid).first()
        if not row:
            return {"error": "Canonicalization not found"}
        raw = session.query(RawWorkflowExtraction).filter_by(extraction_id=row.raw_extraction_id).first()
        if not raw or not raw.graph:
            return {"error": "Source raw workflow is unavailable"}
        return {
            "canonicalization_id": str(cid),
            "paper_id": str(row.project_id),
            "raw_extraction_id": str(raw.extraction_id),
            "raw_graph": raw.graph,
            "schema_library": get_schema_library_payload(),
        }


def save_canonical_workflow(canonicalization_id: str, graph: dict) -> dict:
    """Validate and finalize one append-only canonical workflow."""
    cid = _uuid(canonicalization_id)
    if not cid:
        return {"error": "Invalid canonicalization_id"}
    try:
        validated = CanonicalWorkflowGraph.model_validate(graph)
    except ValidationError as exc:
        return {"error": "Canonical workflow validation failed", "details": exc.errors()}
    if validated.canonicalization_id != str(cid):
        return {"error": "graph.canonicalization_id does not match"}
    if any(not node.node_id.startswith(f"canonical:{cid}:n") for node in validated.nodes):
        return {"error": "Canonical node ID convention violated"}
    if any(not edge.edge_id.startswith(f"canonical:{cid}:e") for edge in validated.edges):
        return {"error": "Canonical edge ID convention violated"}

    with SyncSessionLocal() as session:
        row = session.query(CanonicalWorkflow).filter_by(canonicalization_id=cid).first()
        if not row or row.status != "IN_PROGRESS" or row.graph is not None:
            return {"error": "Canonicalization is missing or already finalized"}
        raw = session.query(RawWorkflowExtraction).filter_by(extraction_id=row.raw_extraction_id).first()
        if not raw or not raw.graph:
            return {"error": "Source raw workflow unavailable"}
        if validated.paper_id != str(row.project_id) or validated.raw_extraction_id != str(raw.extraction_id):
            return {"error": "Canonical graph provenance IDs do not match"}
        known_raw_ids = {node["node_id"] for node in raw.graph.get("nodes", [])}
        known_raw_edge_ids = {edge["edge_id"] for edge in raw.graph.get("edges", [])}
        mapped_raw_ids = {
            raw_id
            for mapping in validated.raw_to_canonical_mappings
            for raw_id in mapping.raw_node_ids
        }
        if not mapped_raw_ids.issubset(known_raw_ids):
            return {"error": "Mapping references an unknown raw node"}
        unmatched_raw_ids = {
            raw_id
            for item in validated.unmatched_raw_information
            for raw_id in item.raw_node_ids
        }
        if mapped_raw_ids | unmatched_raw_ids != known_raw_ids:
            return {"error": "Every raw node must be mapped or explicitly preserved as unmatched"}
        referenced_raw_edges = {raw_id for edge in validated.edges for raw_id in edge.raw_edge_ids}
        if not referenced_raw_edges.issubset(known_raw_edge_ids):
            return {"error": "Canonical edge references an unknown raw edge"}
        template_ids = set(get_schema_library_payload()["operation_templates"])
        if any(
            node.operation_template_id and node.operation_template_id not in template_ids
            for node in validated.nodes
        ):
            return {"error": "Canonical node references an unknown operation template"}
        row.graph = validated.model_dump(mode="json")
        row.status = "COMPLETED"
        row.canonicalized_at = datetime.now(timezone.utc)
        session.commit()
        return {
            "status": "completed", "canonicalization_id": str(cid),
            "version": row.version, "node_count": len(validated.nodes),
            "edge_count": len(validated.edges),
        }


CANONICALIZATION_TOOLS = [get_canonicalization_context, save_canonical_workflow]
