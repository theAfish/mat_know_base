"""Contracts for controlled workflow reprocessing."""

from __future__ import annotations

REEXTRACTION_REASONS = {
    "extractor_prompt_changed",
    "low_quality_extraction",
    "new_parser_capability",
    "manual_review_error",
    "schema_evolution_missing_information",
}
RECANONICALIZATION_REASONS = {
    "alias_added", "templates_merged", "slot_added",
    "granularity_relation_added", "schema_version_changed",
    "raw_version_changed", "manual_request",
}
SCOPE_TYPES = {"full", "section", "paragraph", "table", "figure"}


def validate_reextraction_request(reason: str, scope: dict | None) -> dict:
    if reason not in REEXTRACTION_REASONS:
        raise ValueError(f"Unsupported re-extraction reason: {reason}")
    scope = dict(scope or {"type": "full"})
    scope_type = scope.get("type", "full")
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"Unsupported re-extraction scope: {scope_type}")
    if scope_type != "full" and not str(scope.get("selector", "")).strip():
        raise ValueError("Partial re-extraction requires a scope selector")
    scope["type"] = scope_type
    return scope


def recanonicalization_reason_for_proposal(proposal_type: str) -> str:
    return {
        "add_alias": "alias_added",
        "merge_templates": "templates_merged",
        "add_slot": "slot_added",
        "add_granularity_relation": "granularity_relation_added",
    }.get(proposal_type, "schema_version_changed")
