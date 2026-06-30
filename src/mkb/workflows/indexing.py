"""Build and query persisted canonical-workflow retrieval entries."""

from __future__ import annotations

from collections import deque
from typing import Any

QUERY_MODES = {"strict", "alias-expanded", "template-expanded", "granularity-expanded", "evidence-required"}


def normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def build_index_entries(canonical_graph: dict, raw_graph: dict, schema_library: dict) -> list[dict]:
    """Derive direct, reachability, and template entries with evidence."""
    nodes = {node["node_id"]: node for node in canonical_graph.get("nodes", [])}
    edges = canonical_graph.get("edges", [])
    outgoing: dict[str, list[tuple[str, dict]]] = {}
    for edge in edges:
        outgoing.setdefault(edge["source_node"], []).append((edge["target_node"], edge))
    templates = schema_library.get("operation_templates", {})
    raw_edges = {edge["edge_id"]: edge for edge in raw_graph.get("edges", [])}
    graph_granularity = canonical_graph.get("granularity_mappings", [])
    library_granularity = schema_library.get("granularity_relations", [])

    def make_entry(index_type: str, path: list[str], path_edges: list[dict]) -> dict:
        source, target = nodes[path[0]], nodes[path[-1]]
        operations = [nodes[node_id] for node_id in path if nodes[node_id].get("node_kind") == "operation"]
        primary = operations[0] if len(operations) == 1 else None
        template = templates.get((primary or {}).get("operation_template_id"), {})
        raw_edge_ids = list(dict.fromkeys(
            raw_id for edge in path_edges for raw_id in edge.get("raw_edge_ids", [])
        ))
        evidence = []
        for raw_id in raw_edge_ids:
            raw = raw_edges.get(raw_id)
            if raw and raw.get("evidence_text"):
                evidence.append({
                    "raw_edge_id": raw_id,
                    "evidence_text": raw["evidence_text"],
                    "paper_location": raw.get("paper_location"),
                })
        granularity_terms = []
        operation_ids = {op.get("node_id") for op in operations}
        template_ids = {op.get("operation_template_id") for op in operations if op.get("operation_template_id")}
        for relation in [*graph_granularity, *library_granularity]:
            values = {str(value) for value in relation.values() if value is not None}
            if values.intersection(operation_ids | template_ids):
                granularity_terms.extend(values)
        return {
            "index_type": index_type,
            "source_label": normalize(source.get("label")),
            "target_label": normalize(target.get("label")),
            "operation_label": normalize((primary or {}).get("label")) or None,
            "source_schema": source.get("object_schema"),
            "target_schema": target.get("object_schema"),
            "operation_template_id": (primary or {}).get("operation_template_id"),
            "aliases": sorted({normalize(value) for value in template.get("aliases", []) if normalize(value)}),
            "granularity_terms": sorted({normalize(value) for value in granularity_terms if normalize(value)}),
            "path_node_ids": path,
            "raw_edge_ids": raw_edge_ids,
            "evidence": evidence,
        }

    entries = []
    object_ids = [node_id for node_id, node in nodes.items() if node.get("node_kind") == "object"]
    for start in object_ids:
        queue = deque([(start, [start], [])])
        while queue:
            current, path, path_edges = queue.popleft()
            if current != start and current in object_ids:
                operations = [node_id for node_id in path if nodes[node_id].get("node_kind") == "operation"]
                if operations:
                    kind = "direct" if len(path) == 3 else "reachability"
                    entries.append(make_entry(kind, path, path_edges))
                    if len(path) == 3:
                        entries.append(make_entry("template", path, path_edges))
                # Objects can feed later operations, so continue traversal.
            for next_id, edge in outgoing.get(current, []):
                if next_id not in path:
                    queue.append((next_id, path + [next_id], path_edges + [edge]))
    unique = {}
    for entry in entries:
        key = (entry["index_type"], tuple(entry["path_node_ids"]), entry.get("operation_template_id"))
        unique[key] = entry
    return list(unique.values())


def match_index_entry(entry: dict, *, source: str | None, operation: str | None, target: str | None, mode: str) -> tuple[bool, dict]:
    if mode not in QUERY_MODES:
        raise ValueError(f"Unsupported query mode: {mode}")
    source_q, operation_q, target_q = map(normalize, (source, operation, target))
    if mode in {"strict", "evidence-required", "alias-expanded"} and entry["index_type"] != "direct":
        return False, {}
    if mode == "template-expanded" and entry["index_type"] != "template":
        return False, {}
    if mode == "granularity-expanded" and entry["index_type"] not in {"direct", "reachability", "template"}:
        return False, {}

    def equals(query: str, candidates: list[Any]) -> bool:
        return not query or query in {normalize(candidate) for candidate in candidates if candidate is not None}

    source_candidates = [entry.get("source_label")]
    target_candidates = [entry.get("target_label")]
    operation_candidates = [entry.get("operation_label")]
    expansions = []
    if mode in {"template-expanded", "granularity-expanded"}:
        source_candidates.append(entry.get("source_schema"))
        target_candidates.append(entry.get("target_schema"))
        operation_candidates.append(entry.get("operation_template_id"))
        expansions.append("template")
    if mode in {"alias-expanded", "granularity-expanded"}:
        operation_candidates.extend(entry.get("aliases") or [])
        if entry.get("aliases"):
            expansions.append("alias")
    if mode == "granularity-expanded":
        operation_candidates.extend(entry.get("granularity_terms") or [])
        if entry.get("granularity_terms") or entry["index_type"] == "reachability":
            expansions.append("granularity")
    if mode == "evidence-required" and not entry.get("evidence"):
        return False, {}
    matched = (
        equals(source_q, source_candidates)
        and equals(operation_q, operation_candidates)
        and equals(target_q, target_candidates)
    )
    explanation = {
        "reason": f"matched {entry['index_type']} workflow path",
        "path_node_ids": entry.get("path_node_ids", []),
        "raw_edge_ids": entry.get("raw_edge_ids", []),
        "evidence": entry.get("evidence", []),
        "expansions_used": sorted(set(expansions)),
    }
    return matched, explanation
