"""Context, draft persistence, and finalization tools for canonical workflows."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction, WorkflowIndexEntry
from mkb.workflows.canonical_contract import CanonicalWorkflowGraph
from mkb.workflows.schema_library import get_schema_library_payload
from mkb.workflows.indexing import build_index_entries
from mkb.workflows.validation import compact_validation_errors
from mkb.workflows.editing import (
    compact_value as _compact_value,
    replace_by_id as _replace_by_id,
    replace_by_raw_ids as _replace_by_raw_ids,
)


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        return None


def _draft_template(row: CanonicalWorkflow, raw: RawWorkflowExtraction) -> dict[str, Any]:
    return {
        "schema_version": getattr(row, "schema_version", get_schema_library_payload()["schema_version"]),
        "canonicalization_id": str(row.canonicalization_id),
        "paper_id": str(row.project_id),
        "raw_extraction_id": str(raw.extraction_id),
        "nodes": [],
        "edges": [],
        "raw_to_canonical_mappings": [],
        "unmatched_raw_information": [],
        "granularity_mappings": [],
        "proposed_schema_updates": [],
    }


def _raw_graph_context(raw_graph: dict) -> dict:
    nodes = raw_graph.get("nodes") if isinstance(raw_graph.get("nodes"), list) else []
    edges = raw_graph.get("edges") if isinstance(raw_graph.get("edges"), list) else []
    return {
        "schema_version": raw_graph.get("schema_version"),
        "paper_id": raw_graph.get("paper_id"),
        "extraction_id": raw_graph.get("extraction_id"),
        "nodes": [
            {
                "node_id": node.get("node_id"),
                "raw_name": node.get("raw_name"),
                "canonical_name": node.get("canonical_name"),
                "node_kind": node.get("node_kind") or node.get("node_kind_guess"),
                "semantic_type": node.get("semantic_type"),
                "parameters": _compact_value(node.get("parameters", {}), string_limit=500, list_limit=15, dict_limit=20),
                "identity": _compact_value(node.get("identity", {}), string_limit=500, list_limit=15, dict_limit=20),
                "state": _compact_value(node.get("state", {}), string_limit=500, list_limit=15, dict_limit=20),
                "role": _compact_value(node.get("role", {}), string_limit=500, list_limit=15, dict_limit=20),
                "context": _compact_value(node.get("context", {}), string_limit=500, list_limit=15, dict_limit=20),
                "evidence_text": _compact_value(node.get("evidence_text", ""), string_limit=800),
                "confidence": node.get("confidence"),
            }
            for node in nodes
            if isinstance(node, dict)
        ],
        "edges": [
            {
                "edge_id": edge.get("edge_id"),
                "source_node": edge.get("source_node"),
                "target_node": edge.get("target_node"),
                "relation_type": edge.get("relation_type"),
                "evidence_text": _compact_value(edge.get("evidence_text", ""), string_limit=600),
                "confidence": edge.get("confidence"),
            }
            for edge in edges
            if isinstance(edge, dict)
        ],
        "reproducibility": _compact_value(raw_graph.get("reproducibility", {}), string_limit=600, list_limit=20, dict_limit=20),
        "unresolved_information": _compact_value(raw_graph.get("unresolved_information", []), string_limit=600, list_limit=30, dict_limit=20),
        "note": "This is a compact raw workflow context; final validation still uses the full server-side raw graph.",
    }


def _normalize_canonical_payload(payload: dict, row: CanonicalWorkflow, raw: RawWorkflowExtraction) -> dict:
    normalized = deepcopy(payload if isinstance(payload, dict) else {})
    normalized["schema_version"] = row.schema_version
    normalized["canonicalization_id"] = str(row.canonicalization_id)
    normalized["paper_id"] = str(row.project_id)
    normalized["raw_extraction_id"] = str(raw.extraction_id)
    for key in (
        "nodes", "edges", "raw_to_canonical_mappings", "unmatched_raw_information",
        "granularity_mappings", "proposed_schema_updates",
    ):
        if not isinstance(normalized.get(key), list):
            normalized[key] = []
    return normalized


def _load_row_and_raw(session, canonicalization_id: str):
    cid = _uuid(canonicalization_id)
    if not cid:
        return None, None, {"error": "Invalid canonicalization_id"}
    row = session.query(CanonicalWorkflow).filter_by(canonicalization_id=cid).first()
    if not row:
        return None, None, {"error": "Canonicalization not found"}
    raw = session.query(RawWorkflowExtraction).filter_by(extraction_id=row.raw_extraction_id).first()
    if not raw or not raw.graph:
        return row, raw, {"error": "Source raw workflow is unavailable"}
    return row, raw, None


def _ensure_draft(row: CanonicalWorkflow, raw: RawWorkflowExtraction) -> dict[str, Any]:
    checkpoint = row.checkpoint or {}
    draft = checkpoint.get("graph")
    if isinstance(draft, dict):
        return deepcopy(draft)
    return _draft_template(row, raw)


def _compact_draft_manifest(draft: dict[str, Any], raw_graph: dict) -> dict[str, Any]:
    """Describe resume state without returning the full draft into LLM history."""
    nodes = draft.get("nodes") or []
    edges = draft.get("edges") or []
    mappings = draft.get("raw_to_canonical_mappings") or []
    unmatched = draft.get("unmatched_raw_information") or []
    covered_raw_ids = {
        raw_id for item in [*mappings, *unmatched]
        for raw_id in (item.get("raw_node_ids") or [])
    }
    remaining = [
        {
            "node_id": node.get("node_id"),
            "raw_name": node.get("raw_name"),
            "node_kind_guess": node.get("node_kind_guess"),
        }
        for node in raw_graph.get("nodes", [])
        if node.get("node_id") not in covered_raw_ids
    ]
    return {
        "counts": {
            "nodes": len(nodes), "edges": len(edges),
            "mappings": len(mappings), "unmatched": len(unmatched),
            "remaining_raw_nodes": len(remaining),
        },
        "canonical_nodes": [{
            "node_id": node.get("node_id"), "label": node.get("label"),
            "node_kind": node.get("node_kind"),
            "raw_node_ids": node.get("raw_node_ids", []),
        } for node in nodes],
        "canonical_edges": [{
            "edge_id": edge.get("edge_id"),
            "source_node": edge.get("source_node"),
            "target_node": edge.get("target_node"),
            "relation_type": edge.get("relation_type"),
        } for edge in edges],
        "mapped_raw_node_ids": sorted(covered_raw_ids),
        "remaining_raw_nodes": remaining,
        "note": (
            "The full draft remains server-side. Continue with batch upserts and call "
            "save_canonical_workflow without a graph to finalize that draft."
        ),
    }


def _save_draft(row: CanonicalWorkflow, draft: dict[str, Any], *, summary: str | None = None) -> dict[str, Any]:
    previous = row.checkpoint or {}
    existing_summary = str(previous.get("summary") or "").strip()
    row.checkpoint = {
        "summary": summary.strip() if isinstance(summary, str) and summary.strip() else existing_summary,
        "graph": draft,
    }
    row.checkpoint_updated_at = datetime.now(timezone.utc)
    checkpoint_count = int((row.provenance or {}).get("checkpoint_count", 0)) + 1
    row.provenance = {
        **(row.provenance or {}),
        "checkpoint_count": checkpoint_count,
        "last_checkpoint_summary": str(row.checkpoint.get("summary") or "")[:500],
    }
    return {
        "status": "checkpointed",
        "canonicalization_id": str(row.canonicalization_id),
        "version": row.version,
        "checkpoint_count": checkpoint_count,
    }


def get_canonicalization_context(canonicalization_id: str) -> dict:
    """Load the raw graph and current schema library for a pending run."""
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        return {
            "canonicalization_id": str(row.canonicalization_id),
            "paper_id": str(row.project_id),
            "raw_extraction_id": str(raw.extraction_id),
            "raw_graph": _raw_graph_context(raw.graph or {}),
            "schema_library": get_schema_library_payload(row.schema_version),
        }


def get_canonical_workflow_checkpoint(canonicalization_id: str) -> dict:
    """Read the latest saved checkpoint for an unfinished canonical workflow."""
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        checkpoint = row.checkpoint or {}
        draft = checkpoint.get("graph") if isinstance(checkpoint.get("graph"), dict) else {}
        return {
            "canonicalization_id": str(row.canonicalization_id),
            "project_id": str(row.project_id),
            "raw_extraction_id": str(raw.extraction_id),
            "version": row.version,
            "status": row.status,
            "checkpoint": {
                "summary": checkpoint.get("summary"),
                "manifest": _compact_draft_manifest(draft, raw.graph),
            },
            "checkpoint_updated_at": (
                row.checkpoint_updated_at.isoformat() if row.checkpoint_updated_at else None
            ),
        }


def checkpoint_canonical_workflow(canonicalization_id: str, summary: str) -> dict:
    """Persist a resumable checkpoint for an unfinished canonical workflow."""
    if not isinstance(summary, str) or not summary.strip():
        return {"error": "summary is required"}

    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        if row.graph is not None or row.status not in {"IN_PROGRESS", "FAILED"}:
            return {"error": "This canonicalization can no longer accept checkpoints"}
        draft = _ensure_draft(row, raw)
        result = _save_draft(row, draft, summary=summary)
        session.commit()
        return {
            **result,
            "has_graph": True,
        }


def upsert_canonical_node(canonicalization_id: str, node: dict) -> dict:
    """Add or replace one draft canonical node by node_id."""
    if not isinstance(node, dict):
        return {"error": "node must be a JSON object"}
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        if row.graph is not None:
            return {"error": "Canonicalization is already finalized"}
        draft = _ensure_draft(row, raw)
        try:
            draft["nodes"], action = _replace_by_id(list(draft.get("nodes") or []), "node_id", node)
        except ValueError as exc:
            return {"error": str(exc)}
        result = _save_draft(row, draft)
        session.commit()
        return {**result, "action": action, "node_id": node.get("node_id")}


def upsert_canonical_edge(canonicalization_id: str, edge: dict) -> dict:
    """Add or replace one draft canonical edge by edge_id."""
    if not isinstance(edge, dict):
        return {"error": "edge must be a JSON object"}
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        if row.graph is not None:
            return {"error": "Canonicalization is already finalized"}
        draft = _ensure_draft(row, raw)
        try:
            draft["edges"], action = _replace_by_id(list(draft.get("edges") or []), "edge_id", edge)
        except ValueError as exc:
            return {"error": str(exc)}
        result = _save_draft(row, draft)
        session.commit()
        return {**result, "action": action, "edge_id": edge.get("edge_id")}


def upsert_raw_to_canonical_mapping(canonicalization_id: str, mapping: dict) -> dict:
    """Add or replace one draft raw-to-canonical mapping by raw_node_ids."""
    if not isinstance(mapping, dict):
        return {"error": "mapping must be a JSON object"}
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        if row.graph is not None:
            return {"error": "Canonicalization is already finalized"}
        draft = _ensure_draft(row, raw)
        try:
            draft["raw_to_canonical_mappings"], action = _replace_by_raw_ids(
                list(draft.get("raw_to_canonical_mappings") or []),
                mapping,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        result = _save_draft(row, draft)
        session.commit()
        return {**result, "action": action, "raw_node_ids": mapping.get("raw_node_ids")}


def upsert_unmatched_raw_information(canonicalization_id: str, item: dict) -> dict:
    """Add or replace one unmatched-raw entry by raw_node_ids."""
    if not isinstance(item, dict):
        return {"error": "item must be a JSON object"}
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        if row.graph is not None:
            return {"error": "Canonicalization is already finalized"}
        draft = _ensure_draft(row, raw)
        try:
            draft["unmatched_raw_information"], action = _replace_by_raw_ids(
                list(draft.get("unmatched_raw_information") or []),
                item,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        result = _save_draft(row, draft)
        session.commit()
        return {**result, "action": action, "raw_node_ids": item.get("raw_node_ids")}


def upsert_canonical_draft_batch(
    canonicalization_id: str,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
    mappings: list[dict] | None = None,
    unmatched_items: list[dict] | None = None,
    granularity_mappings: list[dict] | None = None,
    proposed_schema_updates: list[dict] | None = None,
    summary: str | None = None,
) -> dict:
    """Upsert a batch of draft sections in one transaction/checkpoint."""
    sections = {
        "nodes": nodes if nodes is not None else [],
        "edges": edges if edges is not None else [],
        "mappings": mappings if mappings is not None else [],
        "unmatched_items": unmatched_items if unmatched_items is not None else [],
    }
    if any(not isinstance(items, list) for items in sections.values()):
        return {"error": "batch sections must be JSON arrays"}
    if granularity_mappings is not None and not isinstance(granularity_mappings, list):
        return {"error": "granularity_mappings must be a JSON array"}
    if proposed_schema_updates is not None and not isinstance(proposed_schema_updates, list):
        return {"error": "proposed_schema_updates must be a JSON array"}
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        if row.graph is not None:
            return {"error": "Canonicalization is already finalized"}
        draft = _ensure_draft(row, raw)
        counts = {"nodes": 0, "edges": 0, "mappings": 0, "unmatched_items": 0}
        try:
            current_nodes = list(draft.get("nodes") or [])
            for node in sections["nodes"]:
                current_nodes, _ = _replace_by_id(current_nodes, "node_id", node)
                counts["nodes"] += 1
            draft["nodes"] = current_nodes

            current_edges = list(draft.get("edges") or [])
            for edge in sections["edges"]:
                current_edges, _ = _replace_by_id(current_edges, "edge_id", edge)
                counts["edges"] += 1
            draft["edges"] = current_edges

            current_mappings = list(draft.get("raw_to_canonical_mappings") or [])
            for mapping in sections["mappings"]:
                current_mappings, _ = _replace_by_raw_ids(current_mappings, mapping)
                counts["mappings"] += 1
            draft["raw_to_canonical_mappings"] = current_mappings

            current_unmatched = list(draft.get("unmatched_raw_information") or [])
            for item in sections["unmatched_items"]:
                current_unmatched, _ = _replace_by_raw_ids(current_unmatched, item)
                counts["unmatched_items"] += 1
            draft["unmatched_raw_information"] = current_unmatched
        except (TypeError, ValueError) as exc:
            return {"error": str(exc)}
        if granularity_mappings is not None:
            draft["granularity_mappings"] = granularity_mappings
        if proposed_schema_updates is not None:
            draft["proposed_schema_updates"] = proposed_schema_updates
        result = _save_draft(row, draft, summary=summary)
        session.commit()
        return {
            **result, "upserted": counts,
            "node_count": len(draft["nodes"]),
            "edge_count": len(draft["edges"]),
            "mapping_count": len(draft["raw_to_canonical_mappings"]),
            "unmatched_count": len(draft["unmatched_raw_information"]),
        }


def replace_granularity_mappings(canonicalization_id: str, items: list[dict]) -> dict:
    """Replace the draft granularity_mappings list."""
    if not isinstance(items, list):
        return {"error": "items must be a JSON array"}
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        if row.graph is not None:
            return {"error": "Canonicalization is already finalized"}
        draft = _ensure_draft(row, raw)
        draft["granularity_mappings"] = items
        result = _save_draft(row, draft)
        session.commit()
        return {**result, "count": len(items)}


def replace_proposed_schema_updates(canonicalization_id: str, items: list[dict]) -> dict:
    """Replace the draft proposed_schema_updates list."""
    if not isinstance(items, list):
        return {"error": "items must be a JSON array"}
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        if row.graph is not None:
            return {"error": "Canonicalization is already finalized"}
        draft = _ensure_draft(row, raw)
        draft["proposed_schema_updates"] = items
        result = _save_draft(row, draft)
        session.commit()
        return {**result, "count": len(items)}


def save_canonical_workflow(canonicalization_id: str, graph: dict | None = None) -> dict:
    """Validate and finalize one append-only canonical workflow."""
    with SyncSessionLocal() as session:
        row, raw, error = _load_row_and_raw(session, canonicalization_id)
        if error:
            return error
        assert row is not None and raw is not None
        if row.status != "IN_PROGRESS" or row.graph is not None:
            return {"error": "Canonicalization is missing or already finalized"}

        payload = graph if isinstance(graph, dict) else _ensure_draft(row, raw)
        if not isinstance(payload, dict):
            return {"error": "graph must be a JSON object"}
        payload = _normalize_canonical_payload(payload, row, raw)

        try:
            validated = CanonicalWorkflowGraph.model_validate(payload)
        except ValidationError as exc:
            return {
                "error": "Canonical workflow validation failed",
                "details": compact_validation_errors(exc),
            }

        cid = row.canonicalization_id
        if validated.canonicalization_id != str(cid):
            return {"error": "graph.canonicalization_id does not match"}
        if any(not node.node_id.startswith(f"canonical:{cid}:n") for node in validated.nodes):
            return {"error": "Canonical node ID convention violated"}
        if any(not edge.edge_id.startswith(f"canonical:{cid}:e") for edge in validated.edges):
            return {"error": "Canonical edge ID convention violated"}
        if validated.paper_id != str(row.project_id) or validated.raw_extraction_id != str(raw.extraction_id):
            return {"error": "Canonical graph provenance IDs do not match"}
        if validated.schema_version != row.schema_version:
            return {"error": "Canonical graph schema version does not match the run"}

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
        schema_library = get_schema_library_payload(row.schema_version)
        template_ids = set(schema_library["operation_templates"])
        if any(
            node.operation_template_id and node.operation_template_id not in template_ids
            for node in validated.nodes
        ):
            return {"error": "Canonical node references an unknown operation template"}

        row.graph = validated.model_dump(mode="json")
        row.checkpoint = None
        row.status = "COMPLETED"
        row.canonicalized_at = datetime.now(timezone.utc)
        session.query(WorkflowIndexEntry).filter_by(canonicalization_id=cid).delete()
        for entry in build_index_entries(row.graph, raw.graph, schema_library):
            session.add(WorkflowIndexEntry(
                canonicalization_id=cid,
                project_id=row.project_id,
                **entry,
            ))
        session.commit()
        return {
            "status": "completed",
            "canonicalization_id": str(cid),
            "version": row.version,
            "node_count": len(validated.nodes),
            "edge_count": len(validated.edges),
        }


CANONICALIZATION_TOOLS = [
    get_canonicalization_context,
    get_canonical_workflow_checkpoint,
    checkpoint_canonical_workflow,
    upsert_canonical_node,
    upsert_canonical_edge,
    upsert_raw_to_canonical_mapping,
    upsert_unmatched_raw_information,
    upsert_canonical_draft_batch,
    replace_granularity_mappings,
    replace_proposed_schema_updates,
    save_canonical_workflow,
]
