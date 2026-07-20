"""Read models and sampling policies for schema-curator tools."""

from __future__ import annotations

import random
import re
import uuid

from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction, ResearchProject

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


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _latest_reviewable_workflows(session) -> list[RawWorkflowExtraction]:
    rows = (
        session.query(RawWorkflowExtraction)
        .filter(
            RawWorkflowExtraction.status == "COMPLETED",
            RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
        )
        .order_by(RawWorkflowExtraction.project_id, RawWorkflowExtraction.version.desc())
        .all()
    )
    latest: dict[uuid.UUID, RawWorkflowExtraction] = {}
    for row in rows:
        latest.setdefault(row.project_id, row)
    return list(latest.values())


def _workflow_search_text(row: RawWorkflowExtraction, project_label: str | None = None) -> str:
    graph = row.graph or {}
    node_names = " ".join(
        filter(
            None,
            (
                node.get("canonical_name") or node.get("raw_name")
                for node in graph.get("nodes", [])[:120]
            ),
        )
    )
    return _normalize_text(f"{project_label or ''} {row.project_id} {node_names}")


def _serialize_workflow_summary(row: RawWorkflowExtraction, project_label: str | None = None) -> dict:
    graph = row.graph or {}
    review_count = int((row.provenance or {}).get("workflow_review_count", 0))
    node_count = len(graph.get("nodes", []))
    unmapped = sum(
        1 for node in graph.get("nodes", [])
        if (node.get("ontology_status") or "unmapped") != "matched"
    )
    return {
        "workflow_id": str(row.extraction_id),
        "project_id": str(row.project_id),
        "project_label": project_label,
        "version": row.version,
        "record_status": row.record_status,
        "schema_version": row.schema_version,
        "node_count": node_count,
        "edge_count": len(graph.get("edges", [])),
        "unmapped_node_count": unmapped,
        "review_count": review_count,
        "review_flags": row.review_flags or [],
        "sample_labels": [
            node.get("canonical_name") or node.get("raw_name")
            for node in graph.get("nodes", [])[:8]
        ],
    }


def _serialize_workflow_detail(row: RawWorkflowExtraction, project_label: str | None = None) -> dict:
    payload = _card_workflow_evidence(row)
    payload.update({
        "project_label": project_label,
        "version": row.version,
        "record_status": row.record_status,
        "review_flags": row.review_flags or [],
        "review_count": int((row.provenance or {}).get("workflow_review_count", 0)),
    })
    return payload


def _project_labels_by_id(session, project_ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    if not project_ids:
        return {}
    return {
        row.project_id: row.label
        for row in session.query(ResearchProject).filter(
            ResearchProject.project_id.in_(project_ids)
        ).all()
    }


def _weighted_local_sample(rows: list[RawWorkflowExtraction], sample_size: int) -> list[RawWorkflowExtraction]:
    pool = list(rows)
    chosen: list[RawWorkflowExtraction] = []
    target = min(max(1, int(sample_size)), len(pool))
    while pool and len(chosen) < target:
        weights = []
        for row in pool:
            review_count = int((row.provenance or {}).get("workflow_review_count", 0))
            review_flag_bonus = 4 if row.record_status == "needs_review" else 1
            weights.append(max(1, review_flag_bonus * (6 - min(review_count, 5))))
        pick = random.choices(pool, weights=weights, k=1)[0]
        chosen.append(pick)
        pool = [row for row in pool if row.extraction_id != pick.extraction_id]
    return chosen



