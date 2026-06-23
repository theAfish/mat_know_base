"""Evidence-driven workflow schema curator and proposal validation."""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from difflib import SequenceMatcher
ALLOWED_PROPOSAL_TYPES = {
    "create_template", "merge_templates", "add_alias", "add_slot",
    "add_granularity_relation", "deprecate_template",
}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def analyze_canonical_workflows(workflows: list[dict], *, min_support: int = 2) -> list[dict]:
    """Find repeated unmatched operations and emit evidence-backed proposals."""
    unmatched: dict[str, list[str]] = defaultdict(list)
    parameter_slots: dict[tuple[str, str], list[str]] = defaultdict(list)
    signatures: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    expansions: dict[tuple[str, str], list[str]] = defaultdict(list)
    mappings: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    explicit: dict[tuple[str, str], dict] = {}
    object_patterns: dict[str, list[str]] = defaultdict(list)

    for workflow in workflows:
        graph = workflow.get("graph") or {}
        wid = str(workflow.get("canonicalization_id") or graph.get("canonicalization_id"))
        raw_by_id = {n.get("node_id"): n for n in (workflow.get("raw_graph") or {}).get("nodes", [])}
        nodes = {n.get("node_id"): n for n in graph.get("nodes", [])}
        for item in graph.get("unmatched_raw_information", []):
            for raw_id in item.get("raw_node_ids", []):
                raw = raw_by_id.get(raw_id, {})
                if raw.get("node_kind_guess") == "operation":
                    name = str(raw.get("raw_name", "")).strip()
                    if name:
                        unmatched[name.casefold()].append(wid)
                elif raw.get("node_kind_guess") == "object":
                    name = str(raw.get("raw_name", "")).casefold().strip()
                    if name:
                        object_patterns[name].append(wid)
        for node in nodes.values():
            template = node.get("operation_template_id")
            if template:
                for slot in (node.get("attributes") or {}):
                    parameter_slots[(template, slot)].append(wid)
        for node in nodes.values():
            if node.get("node_kind") != "operation":
                continue
            inputs = [nodes.get(e.get("source_node"), {}).get("object_schema") for e in graph.get("edges", []) if e.get("target_node") == node.get("node_id")]
            outputs = [nodes.get(e.get("target_node"), {}).get("object_schema") for e in graph.get("edges", []) if e.get("source_node") == node.get("node_id")]
            for source in filter(None, inputs):
                for target in filter(None, outputs):
                    signatures[(source, node.get("operation_template_id") or node.get("label", ""), target)].append(wid)
        for relation in graph.get("granularity_mappings", []):
            pair = (str(relation.get("coarse")), str(relation.get("fine")))
            expansions[pair].append(wid)
        for mapping in graph.get("raw_to_canonical_mappings", []):
            for raw_id in mapping.get("raw_node_ids", []):
                for canonical_id in mapping.get("canonical_node_ids", []):
                    mappings[raw_id][canonical_id].append(wid)
        for candidate in graph.get("proposed_schema_updates", []):
            kind = candidate.get("proposal_type") or candidate.get("type")
            payload = candidate.get("payload", {})
            if kind in ALLOWED_PROPOSAL_TYPES and payload:
                key = (kind, repr(sorted(payload.items())))
                saved = explicit.setdefault(key, {"proposal_type": kind, "payload": payload, "evidence": []})
                saved["evidence"].append(wid)

    proposals = []
    for name, evidence in unmatched.items():
        evidence = sorted(set(evidence))
        if len(evidence) >= min_support:
            payload = {
                "slug": slugify(name), "label": name, "aliases": [],
            }
            proposals.append(_proposal("create_template", payload, evidence, {"signal": "frequent_unmatched_operation", "support": len(evidence)}))
    clustered = set()
    unmatched_names = sorted(unmatched)
    for index, name in enumerate(unmatched_names):
        if name in clustered:
            continue
        aliases = [other for other in unmatched_names[index + 1:] if SequenceMatcher(None, name, other).ratio() >= 0.82]
        evidence = sorted(set(unmatched[name] + [wid for alias in aliases for wid in unmatched[alias]]))
        if aliases and len(evidence) >= min_support:
            clustered.update([name, *aliases])
            payload = {
                "slug": slugify(name), "label": name,
                "aliases": aliases,
            }
            proposals.append(_proposal("create_template", payload, evidence, {"signal": "synonym_cluster", "support": len(evidence)}))
    for (template, slot), evidence in parameter_slots.items():
        evidence = sorted(set(evidence))
        if len(evidence) >= min_support:
            proposals.append(_proposal("add_slot", {"template_id": template, "slot": slot}, evidence, {"signal": "repeated_parameter_slot", "support": len(evidence)}))
    for (coarse, fine), evidence in expansions.items():
        evidence = sorted(set(evidence))
        if coarse and fine and len(evidence) >= min_support:
            proposals.append(_proposal("add_granularity_relation", {"coarse": coarse, "fine": fine}, evidence, {"signal": "recurring_expansion", "support": len(evidence)}))
    for item in explicit.values():
        evidence = sorted(set(item["evidence"]))
        if len(evidence) >= min_support:
            proposals.append(_proposal(item["proposal_type"], item["payload"], evidence, {"signal": "repeated_explicit_candidate", "support": len(evidence)}))
    # Attach corroborating signals so reviewers can see object, signature, and
    # conflict analysis without unsafe automatic ontology changes.
    diagnostics = {
        "frequent_object_patterns": {k: sorted(set(v)) for k, v in object_patterns.items() if len(set(v)) >= min_support},
        "repeated_io_signatures": {" | ".join(map(str, k)): sorted(set(v)) for k, v in signatures.items() if len(set(v)) >= min_support},
        "conflicting_mappings": {raw: sorted(options) for raw, options in mappings.items() if len(options) > 1},
    }
    for proposal in proposals:
        proposal["analysis"]["diagnostics"] = diagnostics
    return proposals


def _proposal(kind: str, payload: dict, evidence: list[str], analysis: dict) -> dict:
    return {"proposal_type": kind, "payload": payload, "evidence_workflow_ids": evidence, "analysis": analysis}


def validate_proposal(proposal_type: str, payload: dict, evidence_workflow_ids: list[str], library: dict) -> list[str]:
    errors = []
    if proposal_type not in ALLOWED_PROPOSAL_TYPES:
        errors.append("unsupported proposal type")
    if not evidence_workflow_ids:
        errors.append("at least one evidence workflow is required")
    templates = library.get("operation_templates", {})
    if proposal_type == "create_template" and (not payload.get("slug") or not payload.get("label")):
        errors.append("create_template requires slug and label")
    if proposal_type == "create_template":
        parameters = payload.get("parameters", {})
        if not isinstance(parameters, dict):
            errors.append("parameters must be an object of evidence-supported key/value pairs")
        elif any(not isinstance(key, str) or not key.strip() for key in parameters):
            errors.append("parameter keys must be non-empty strings")
        slots = payload.get("slots", [])
        if not isinstance(slots, list):
            errors.append("slots must be a list")
        else:
            for slot in slots:
                if isinstance(slot, str) and slot.strip():
                    continue
                if isinstance(slot, dict) and str(slot.get("key", "")).strip():
                    continue
                errors.append("each slot must be a non-empty key or an object containing key")
                break
    if proposal_type in {"add_alias", "add_slot", "deprecate_template"} and payload.get("template_id") not in templates:
        errors.append("template_id does not exist")
    if proposal_type == "add_alias" and not str(payload.get("alias", "")).strip():
        errors.append("add_alias requires alias")
    if proposal_type == "add_slot":
        slot = payload.get("slot")
        if not (
            (isinstance(slot, str) and slot.strip())
            or (isinstance(slot, dict) and str(slot.get("key", "")).strip())
        ):
            errors.append("add_slot requires a key string or an object containing key")
    if proposal_type == "merge_templates":
        if payload.get("source_template_id") not in templates or payload.get("target_template_id") not in templates:
            errors.append("merge source and target templates must exist")
        if payload.get("source_template_id") == payload.get("target_template_id"):
            errors.append("cannot merge a template into itself")
    if proposal_type == "add_granularity_relation" and (not payload.get("coarse") or not payload.get("fine")):
        errors.append("granularity relation requires coarse and fine values")
    return errors


def apply_proposal(library: dict, proposal_type: str, payload: dict) -> dict:
    result = copy.deepcopy(library)
    templates = result.setdefault("operation_templates", {})
    if proposal_type == "create_template":
        template_id = f"operation-template:{result['schema_version']}:{payload['slug']}"
        if template_id in templates:
            raise ValueError("template already exists")
        templates[template_id] = {
            key: value for key, value in payload.items() if key != "slug"
        }
        templates[template_id].setdefault("aliases", [])
        templates[template_id].setdefault("slots", [])
        templates[template_id].setdefault("parameters", {})
    elif proposal_type == "add_alias":
        aliases = templates[payload["template_id"]].setdefault("aliases", [])
        if payload["alias"] not in aliases:
            aliases.append(payload["alias"])
    elif proposal_type == "add_slot":
        slots = templates[payload["template_id"]].setdefault("slots", [])
        if payload["slot"] not in slots:
            slots.append(payload["slot"])
    elif proposal_type == "deprecate_template":
        templates[payload["template_id"]]["deprecated"] = True
    elif proposal_type == "merge_templates":
        templates[payload["source_template_id"]]["merged_into"] = payload["target_template_id"]
        templates[payload["source_template_id"]]["deprecated"] = True
    elif proposal_type == "add_granularity_relation":
        result.setdefault("granularity_relations", []).append(payload)
    return result
