"""Persistence tool for the raw workflow extraction agent."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import RawWorkflowExtraction
from mkb.workflows.contract import RawWorkflowGraph
from mkb.workflows.schema_library import get_schema_library_payload
from mkb.workflows.review import audit_raw_graph
from mkb.workflows.validation import compact_validation_errors

SEARCHABLE_NODE_KINDS = {None, "", "object", "operation", "planning", "reasoning", "unknown"}
NODE_KINDS = {"object", "operation", "planning", "reasoning", "unknown"}
RELATION_ALIASES = {
    "input": "input_to",
    "input_to": "input_to",
    "produces": "produces",
    "output": "produces",
    "output_of": "produces",
    "same_as": "same_as",
    "part_of": "part_of",
    "has_part": "has_part",
    "expands_to": "expands_to",
    "summarized_by": "summarized_by",
    "motivates": "motivates",
    "leads_to": "leads_to",
}


def _dict_or_empty(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list:
    return value if isinstance(value, list) else []


def _normalize_kind(value: Any) -> str:
    kind = str(value or "unknown").strip().casefold().replace("-", "_")
    return kind if kind in NODE_KINDS else "unknown"


def _normalize_raw_graph_payload(graph: dict, row: RawWorkflowExtraction, extraction_id: uuid.UUID) -> tuple[dict, dict]:
    """Fill app-owned workflow envelope fields and tolerate common LLM aliases."""
    payload = deepcopy(graph if isinstance(graph, dict) else {})
    changes = {
        "filled_graph_fields": [],
        "assigned_node_ids": 0,
        "assigned_edge_ids": 0,
        "normalized_nodes": 0,
        "normalized_edges": 0,
    }
    for key, value in {
        "schema_version": row.schema_version,
        "paper_id": str(row.project_id),
        "extraction_id": str(extraction_id),
    }.items():
        if payload.get(key) != value:
            payload[key] = value
            changes["filled_graph_fields"].append(key)

    raw_nodes = payload.get("nodes")
    payload["nodes"] = raw_nodes if isinstance(raw_nodes, list) else []
    raw_edges = payload.get("edges")
    payload["edges"] = raw_edges if isinstance(raw_edges, list) else []
    payload["unresolved_information"] = [
        item if isinstance(item, dict) else {"description": str(item)}
        for item in _list_or_empty(payload.get("unresolved_information"))
    ]
    if not isinstance(payload.get("reproducibility"), dict):
        payload.pop("reproducibility", None)

    old_node_refs: dict[str, str] = {}
    for index, node in enumerate(payload["nodes"], 1):
        if not isinstance(node, dict):
            node = {"raw_name": str(node), "evidence_text": str(node)}
            payload["nodes"][index - 1] = node
        original_refs = {
            str(value).strip()
            for value in (
                node.get("node_id"),
                node.get("id"),
                node.get("name"),
                node.get("label"),
                node.get("raw_name"),
                node.get("canonical_name"),
            )
            if value is not None and str(value).strip()
        }
        expected_id = f"raw:{extraction_id}:n{index:04d}"
        node_id = str(node.get("node_id") or node.get("id") or "").strip()
        if not node_id or not node_id.startswith(f"raw:{extraction_id}:n"):
            node["node_id"] = expected_id
            changes["assigned_node_ids"] += 1
        kind = _normalize_kind(node.get("node_kind") or node.get("node_kind_guess") or node.get("kind"))
        node["node_kind"] = kind
        node["node_kind_guess"] = kind
        node["raw_name"] = str(node.get("raw_name") or node.get("canonical_name") or node.get("label") or node["node_id"])
        node.setdefault("canonical_name", node.get("raw_name"))
        node.setdefault("semantic_type", kind)
        for key in ("parameters", "identity", "state", "role", "context", "attributes_explicitly_mentioned", "paper_location"):
            node[key] = _dict_or_empty(node.get(key))
        node["unparsed_modifiers"] = _list_or_empty(node.get("unparsed_modifiers"))
        node["aliases_observed"] = _list_or_empty(node.get("aliases_observed"))
        status = str(node.get("ontology_status") or "unmapped").strip().casefold()
        node["ontology_status"] = "matched" if status == "mapped" else status if status in {"matched", "candidate", "unmapped"} else "unmapped"
        node["evidence_text"] = str(node.get("evidence_text") or node.get("evidence") or node.get("raw_name"))
        try:
            node["confidence"] = float(node.get("confidence", 0.5))
        except (TypeError, ValueError):
            node["confidence"] = 0.5
        node["confidence"] = max(0.0, min(1.0, node["confidence"]))
        for ref in original_refs:
            old_node_refs[ref] = node["node_id"]
        changes["normalized_nodes"] += 1

    for index, edge in enumerate(payload["edges"], 1):
        if not isinstance(edge, dict):
            edge = {"evidence_text": str(edge)}
            payload["edges"][index - 1] = edge
        edge_id = str(edge.get("edge_id") or edge.get("id") or "").strip()
        if not edge_id or not edge_id.startswith(f"raw:{extraction_id}:e"):
            edge["edge_id"] = f"raw:{extraction_id}:e{index:04d}"
            changes["assigned_edge_ids"] += 1
        source = edge.get("source_node", edge.get("source"))
        target = edge.get("target_node", edge.get("target"))
        edge["source_node"] = old_node_refs.get(str(source).strip(), source)
        edge["target_node"] = old_node_refs.get(str(target).strip(), target)
        relation = str(edge.get("relation_type") or edge.get("kind") or edge.get("relation") or "").strip().casefold().replace("-", "_")
        edge["relation_type"] = RELATION_ALIASES.get(relation, relation)
        edge["attributes"] = _dict_or_empty(edge.get("attributes"))
        edge["evidence_text"] = str(edge.get("evidence_text") or edge.get("evidence") or edge.get("relation_type") or "")
        if edge.get("paper_location") is not None:
            edge["paper_location"] = _dict_or_empty(edge.get("paper_location"))
        try:
            edge["confidence"] = float(edge.get("confidence", 0.5))
        except (TypeError, ValueError):
            edge["confidence"] = 0.5
        edge["confidence"] = max(0.0, min(1.0, edge["confidence"]))
        changes["normalized_edges"] += 1

    return payload, changes


def _compact_raw_checkpoint_manifest(graph: dict | None) -> dict:
    if not isinstance(graph, dict):
        return {"counts": {"nodes": 0, "edges": 0}, "nodes": [], "edges": []}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    return {
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "unresolved_information": len(graph.get("unresolved_information") or []),
        },
        "nodes": [
            {
                "node_id": node.get("node_id"),
                "raw_name": node.get("raw_name") or node.get("canonical_name") or node.get("label"),
                "node_kind": node.get("node_kind") or node.get("node_kind_guess") or node.get("kind"),
            }
            for node in nodes[:80]
            if isinstance(node, dict)
        ],
        "edges": [
            {
                "edge_id": edge.get("edge_id"),
                "source_node": edge.get("source_node") or edge.get("source"),
                "target_node": edge.get("target_node") or edge.get("target"),
                "relation_type": edge.get("relation_type") or edge.get("kind") or edge.get("relation"),
            }
            for edge in edges[:120]
            if isinstance(edge, dict)
        ],
        "note": "The full checkpoint graph remains server-side. Continue from this manifest and save/checkpoint only changed draft content.",
    }


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
    if node_kind not in SEARCHABLE_NODE_KINDS:
        return {"error": "node_kind must be one of object, operation, planning, reasoning, unknown, or omitted"}

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
        if node_kind and node_kind != "operation":
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
                    "default dict/list fields, and common edge aliases. Fix the listed node/edge "
                    "semantics rather than resending the full source content."
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
