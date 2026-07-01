"""Raw workflow auditing, lifecycle review, and immutable correction helpers."""

from __future__ import annotations

import copy
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from mkb.workflows.contract import RawWorkflowGraph

VALID_RECORD_STATUSES = {"active", "superseded", "retracted", "needs_review", "known_error"}


def audit_raw_graph(graph: dict, *, low_confidence_threshold: float = 0.5, later_graph: dict | None = None) -> list[dict]:
    """Return deterministic, machine-readable quality flags for a raw graph."""
    flags: list[dict[str, Any]] = []
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    by_id = {node.get("node_id"): node for node in nodes}
    for item_type, items, id_key in (("node", nodes, "node_id"), ("edge", edges, "edge_id")):
        for item in items:
            if float(item.get("confidence", 0)) < low_confidence_threshold:
                flags.append({"type": "low_confidence", "item_type": item_type, "item_id": item.get(id_key)})
            if not str(item.get("evidence_text", "")).strip():
                flags.append({"type": "missing_evidence", "item_type": item_type, "item_id": item.get(id_key)})
    # Repeated names are normal card reuse in v2 (distinct runs of the same
    # operation). Keep the old heuristic only for legacy free-text graphs.
    if graph.get("schema_version") == "raw-workflow/1.0":
        normalized = [str(n.get("raw_name", "")).casefold().strip() for n in nodes]
        duplicates = {name for name, count in Counter(normalized).items() if name and count > 1}
        for node in nodes:
            if str(node.get("raw_name", "")).casefold().strip() in duplicates:
                flags.append({"type": "duplicated_node", "item_type": "node", "item_id": node.get("node_id")})
    for edge in edges:
        source, target = by_id.get(edge.get("source_node")), by_id.get(edge.get("target_node"))
        relation = edge.get("relation_type")
        impossible = not source or not target
        source_kind = (source or {}).get("node_kind") or (source or {}).get("node_kind_guess")
        target_kind = (target or {}).get("node_kind") or (target or {}).get("node_kind_guess")
        impossible |= relation == "input_to" and source_kind != "object"
        impossible |= relation == "input_to" and target_kind != "operation"
        impossible |= relation == "produces" and source_kind != "operation"
        impossible |= relation == "produces" and target_kind != "object"
        impossible |= relation in {"motivates", "leads_to"} and source_kind not in {"planning", "reasoning"}
        if impossible:
            flags.append({"type": "impossible_edge", "item_type": "edge", "item_id": edge.get("edge_id")})
    granular_pairs = {(e.get("source_node"), e.get("target_node")) for e in edges if e.get("relation_type") in {"part_of", "has_part", "expands_to", "summarized_by"}}
    for source, target in granular_pairs:
        if (target, source) in granular_pairs:
            flags.append({"type": "inconsistent_granularity", "item_type": "edge", "item_id": f"{source}:{target}"})
    if later_graph:
        current_names = {str(n.get("raw_name", "")).casefold().strip() for n in nodes}
        later_names = {str(n.get("raw_name", "")).casefold().strip() for n in later_graph.get("nodes", [])}
        for name in sorted(current_names - later_names):
            flags.append({"type": "conflict_with_later_extraction", "item_type": "node", "item_id": name})
    return flags


def rebase_graph(graph: dict, extraction_id: uuid.UUID) -> dict:
    """Copy a correction graph and assign IDs belonging to its new immutable version."""
    result = copy.deepcopy(graph)
    old_to_new = {}
    for index, node in enumerate(result.get("nodes", []), 1):
        old_to_new[node["node_id"]] = f"raw:{extraction_id}:n{index:04d}"
        node["node_id"] = old_to_new[node["node_id"]]
    for index, edge in enumerate(result.get("edges", []), 1):
        edge["edge_id"] = f"raw:{extraction_id}:e{index:04d}"
        edge["source_node"] = old_to_new.get(edge["source_node"], edge["source_node"])
        edge["target_node"] = old_to_new.get(edge["target_node"], edge["target_node"])
    result["extraction_id"] = str(extraction_id)
    return RawWorkflowGraph.model_validate(result).model_dump(mode="json")


def correction_metadata(reason: str, author: str, affected_nodes: list[str], affected_edges: list[str], evidence: str) -> dict:
    if not reason.strip() or not author.strip() or not evidence.strip():
        raise ValueError("correction reason, author, and evidence are required")
    return {
        "affected_nodes": affected_nodes,
        "affected_edges": affected_edges,
        "evidence": evidence,
        "corrected_at": datetime.now(timezone.utc).isoformat(),
    }
