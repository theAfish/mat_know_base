"""Low-level workflow editing helpers shared by agent tool adapters."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from mkb.workflows.schema_library import get_schema_library_payload

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
STRUCTURAL_RELATIONS = {"same_as", "part_of", "has_part", "expands_to", "summarized_by"}
ENDPOINT_ALIASES = {
    "source": (
        "source_node",
        "source",
        "source_id",
        "source_node_id",
        "from",
        "from_node",
        "from_node_id",
        "source_label",
        "from_label",
    ),
    "target": (
        "target_node",
        "target",
        "target_id",
        "target_node_id",
        "to",
        "to_node",
        "to_node_id",
        "target_label",
        "to_label",
    ),
}


def dict_or_empty(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def list_or_empty(value: Any) -> list:
    return value if isinstance(value, list) else []


def normalize_node_kind(value: Any) -> str:
    kind = str(value or "unknown").strip().casefold().replace("-", "_")
    return kind if kind in NODE_KINDS else "unknown"


def infer_relation_type(source_kind: str | None, target_kind: str | None, requested: str | None = None) -> str | None:
    """Infer deterministic workflow relations from endpoint node kinds."""
    requested_key = str(requested or "").strip().casefold().replace("-", "_")
    requested = RELATION_ALIASES.get(requested_key, requested_key)
    if source_kind == "object" and target_kind == "operation":
        return "input_to"
    if source_kind == "operation" and target_kind == "object":
        return "produces"
    if source_kind in {"planning", "reasoning"}:
        return "leads_to" if requested == "leads_to" else "motivates"
    if requested in STRUCTURAL_RELATIONS:
        return requested
    return None


def _edge_endpoint(edge: dict[str, Any], side: str) -> Any:
    for key in ENDPOINT_ALIASES[side]:
        if key in edge and edge.get(key) not in (None, ""):
            return edge.get(key)
    return None


def _endpoint_refs(value: Any) -> list[str]:
    if isinstance(value, dict):
        refs = []
        for key in ("node_id", "id", "name", "label", "raw_name", "canonical_name"):
            if value.get(key) not in (None, ""):
                refs.append(str(value[key]).strip())
        return refs
    if isinstance(value, int):
        return [str(value), f"n{value}", f"node_{value}", f"node-{value}", f"node {value}"]
    if value is None:
        return []
    text = str(value).strip()
    refs = [text]
    if text.isdigit():
        refs.extend([f"n{text}", f"node_{text}", f"node-{text}", f"node {text}"])
    return refs


def _resolve_node_ref(value: Any, old_node_refs: dict[str, str]) -> str | None:
    for ref in _endpoint_refs(value):
        if ref in old_node_refs:
            return old_node_refs[ref]
    refs = _endpoint_refs(value)
    return refs[0] if refs else None


def compact_value(
    value: Any,
    *,
    string_limit: int = 1000,
    list_limit: int = 30,
    dict_limit: int = 30,
) -> Any:
    if isinstance(value, str):
        return value if len(value) <= string_limit else f"{value[:string_limit]}... [truncated]"
    if isinstance(value, list):
        items = [
            compact_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=dict_limit,
            )
            for item in value[:list_limit]
        ]
        if len(value) > list_limit:
            items.append({"omitted_items": len(value) - list_limit})
        return items
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= dict_limit:
                result["omitted_keys"] = len(value) - dict_limit
                break
            result[key] = compact_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=dict_limit,
            )
        return result
    return value


def replace_by_id(items: list[dict[str, Any]], id_key: str, item: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    item_id = item.get(id_key)
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError(f"{id_key} is required")
    for index, existing in enumerate(items):
        if existing.get(id_key) == item_id:
            items[index] = item
            return items, "updated"
    items.append(item)
    return items, "added"


def replace_by_raw_ids(items: list[dict[str, Any]], item: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    raw_ids = tuple(item.get("raw_node_ids") or [])
    if not raw_ids:
        raise ValueError("raw_node_ids is required")
    for index, existing in enumerate(items):
        if tuple(existing.get("raw_node_ids") or []) == raw_ids:
            items[index] = item
            return items, "updated"
    items.append(item)
    return items, "added"


def normalize_raw_graph_payload(graph: dict, row, extraction_id) -> tuple[dict, dict]:
    """Fill app-owned workflow envelope fields and tolerate common LLM aliases."""
    payload = deepcopy(graph if isinstance(graph, dict) else {})
    changes = {
        "filled_graph_fields": [],
        "assigned_node_ids": 0,
        "assigned_edge_ids": 0,
        "inferred_edge_relations": 0,
        "overrode_edge_relations": 0,
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

    payload["nodes"] = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    payload["edges"] = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    payload["unresolved_information"] = [
        item if isinstance(item, dict) else {"description": str(item)}
        for item in list_or_empty(payload.get("unresolved_information"))
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
        kind = normalize_node_kind(node.get("node_kind") or node.get("node_kind_guess") or node.get("kind"))
        node["node_kind"] = kind
        node["node_kind_guess"] = kind
        name = str(node.get("canonical_name") or node.get("raw_name") or node.get("label") or node["node_id"])
        node["canonical_name"] = name
        node["raw_name"] = name
        node.setdefault("semantic_type", kind)
        for key in ("parameters", "identity", "state", "role", "context", "attributes_explicitly_mentioned", "paper_location"):
            node[key] = dict_or_empty(node.get(key))
        node["unparsed_modifiers"] = list_or_empty(node.get("unparsed_modifiers"))
        node["aliases_observed"] = list_or_empty(node.get("aliases_observed"))
        status = str(node.get("ontology_status") or "unmapped").strip().casefold()
        node["ontology_status"] = "matched" if status == "mapped" else status if status in {"matched", "candidate", "unmapped"} else "unmapped"
        node["evidence_text"] = str(node.get("evidence_text") or node.get("evidence") or node.get("raw_name"))
        try:
            node["confidence"] = float(node.get("confidence", 0.5))
        except (TypeError, ValueError):
            node["confidence"] = 0.5
        node["confidence"] = max(0.0, min(1.0, node["confidence"]))
        for key in ("id", "name", "label", "kind", "evidence"):
            node.pop(key, None)
        index_refs = (str(index), f"n{index}", f"node_{index}", f"node-{index}", f"node {index}")
        for ref in (*original_refs, expected_id, *index_refs):
            old_node_refs[ref] = node["node_id"]
        changes["normalized_nodes"] += 1

    node_kinds = {node.get("node_id"): node.get("node_kind") for node in payload["nodes"] if isinstance(node, dict)}
    for index, edge in enumerate(payload["edges"], 1):
        if not isinstance(edge, dict):
            edge = {"evidence_text": str(edge)}
            payload["edges"][index - 1] = edge
        edge_id = str(edge.get("edge_id") or edge.get("id") or "").strip()
        if not edge_id or not edge_id.startswith(f"raw:{extraction_id}:e"):
            edge["edge_id"] = f"raw:{extraction_id}:e{index:04d}"
            changes["assigned_edge_ids"] += 1
        edge.pop("id", None)
        source = _edge_endpoint(edge, "source")
        target = _edge_endpoint(edge, "target")
        edge["source_node"] = _resolve_node_ref(source, old_node_refs) or ""
        edge["target_node"] = _resolve_node_ref(target, old_node_refs) or ""
        for key in (*ENDPOINT_ALIASES["source"], *ENDPOINT_ALIASES["target"]):
            if key not in {"source_node", "target_node"}:
                edge.pop(key, None)
        relation = str(edge.get("relation_type") or edge.get("kind") or edge.get("relation") or "").strip().casefold().replace("-", "_")
        normalized_relation = RELATION_ALIASES.get(relation, relation)
        inferred_relation = infer_relation_type(
            node_kinds.get(edge["source_node"]),
            node_kinds.get(edge["target_node"]),
            normalized_relation,
        )
        if inferred_relation in RELATION_ALIASES.values():
            edge["relation_type"] = inferred_relation
            if inferred_relation != normalized_relation:
                if normalized_relation:
                    changes["overrode_edge_relations"] += 1
                else:
                    changes["inferred_edge_relations"] += 1
        else:
            edge["relation_type"] = normalized_relation or "input_to"
        edge["attributes"] = dict_or_empty(edge.get("attributes"))
        edge["evidence_text"] = str(
            edge.get("evidence_text")
            or edge.get("evidence")
            or edge.get("evidence_quote")
            or edge.get("evidence_snippet")
            or f"{source} -> {target}"
        )
        for key in ("kind", "relation", "evidence", "evidence_quote", "evidence_snippet"):
            edge.pop(key, None)
        if edge.get("paper_location") is not None:
            edge["paper_location"] = dict_or_empty(edge.get("paper_location"))
        try:
            edge["confidence"] = float(edge.get("confidence", 0.5))
        except (TypeError, ValueError):
            edge["confidence"] = 0.5
        edge["confidence"] = max(0.0, min(1.0, edge["confidence"]))
        changes["normalized_edges"] += 1

    return payload, changes


def compact_raw_checkpoint_manifest(graph: dict | None) -> dict:
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


def get_active_workflow_card_library(max_cards: int = 40, max_templates: int = 40) -> dict:
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


def search_workflow_cards(query: str, node_kind: str | None = None, limit: int = 10) -> dict:
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
