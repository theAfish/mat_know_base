"""Persistence tool for the raw workflow extraction agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import RawWorkflowExtraction
from mkb.workflows.contract import RawWorkflowGraph


def save_raw_workflow(extraction_id: str, graph: dict) -> dict:
    """Validate and immutably save one raw workflow extraction graph."""
    try:
        eid = uuid.UUID(extraction_id)
    except (TypeError, ValueError, AttributeError):
        return {"error": f"Invalid extraction_id: {extraction_id}"}

    try:
        validated = RawWorkflowGraph.model_validate(graph)
    except ValidationError as exc:
        return {"error": "Raw workflow contract validation failed", "details": exc.errors()}

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
        row.status = "COMPLETED"
        previous = (
            session.query(RawWorkflowExtraction)
            .filter(
                RawWorkflowExtraction.project_id == row.project_id,
                RawWorkflowExtraction.extraction_id != row.extraction_id,
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.record_status == "active",
            )
            .order_by(RawWorkflowExtraction.version.desc())
            .first()
        )
        if previous:
            previous.record_status = "superseded"
            row.supersedes_extraction_id = previous.extraction_id
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


WORKFLOW_EXTRACTION_TOOLS = [save_raw_workflow]
