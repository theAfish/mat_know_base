import pytest

from mkb.workflows.indexing import build_index_entries, match_index_entry
from mkb.workflows.maintenance import validate_reextraction_request


def _graphs():
    raw = {"edges": [{"edge_id": "r1", "evidence_text": "The sample was annealed.", "paper_location": {"page": 2}}]}
    canonical = {
        "nodes": [
            {"node_id": "o1", "label": "raw sample", "node_kind": "object", "object_schema": "MaterialObject"},
            {"node_id": "p1", "label": "annealing", "node_kind": "operation", "operation_template_id": "anneal"},
            {"node_id": "o2", "label": "annealed sample", "node_kind": "object", "object_schema": "MaterialObject"},
        ],
        "edges": [
            {"source_node": "o1", "target_node": "p1", "raw_edge_ids": ["r1"]},
            {"source_node": "p1", "target_node": "o2", "raw_edge_ids": ["r1"]},
        ],
        "granularity_mappings": [],
    }
    schema = {"operation_templates": {"anneal": {"label": "Annealing", "aliases": ["heat treatment"]}}}
    return canonical, raw, schema


def test_builds_direct_template_and_evidenced_entries():
    entries = build_index_entries(*_graphs())
    assert {entry["index_type"] for entry in entries} == {"direct", "template"}
    direct = next(entry for entry in entries if entry["index_type"] == "direct")
    assert direct["evidence"][0]["raw_edge_id"] == "r1"
    matched, explanation = match_index_entry(
        direct, source="raw sample", operation="heat treatment",
        target="annealed sample", mode="alias-expanded",
    )
    assert matched
    assert "alias" in explanation["expansions_used"]


def test_partial_reextraction_scope_requires_selector():
    with pytest.raises(ValueError, match="selector"):
        validate_reextraction_request("manual_review_error", {"type": "figure"})
    assert validate_reextraction_request(
        "manual_review_error", {"type": "figure", "selector": "Figure 3"}
    )["selector"] == "Figure 3"
