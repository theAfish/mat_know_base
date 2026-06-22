import pytest
from pydantic import ValidationError

from mkb.workflows.canonical_contract import CanonicalWorkflowGraph


def _graph():
    cid = "33333333-3333-3333-3333-333333333333"
    return {
        "schema_version": "workflow-schema/1.0",
        "canonicalization_id": cid,
        "paper_id": "22222222-2222-2222-2222-222222222222",
        "raw_extraction_id": "11111111-1111-1111-1111-111111111111",
        "nodes": [{
            "node_id": f"canonical:{cid}:n0001", "label": "Material",
            "node_kind": "object", "object_schema": "MaterialObject",
            "attributes": {}, "raw_node_ids": ["raw:x:n0001"],
        }],
        "edges": [],
        "raw_to_canonical_mappings": [{
            "raw_node_ids": ["raw:x:n0001"],
            "canonical_node_ids": [f"canonical:{cid}:n0001"],
            "mapping_type": "one_to_one", "confidence": 0.9,
            "justification": "Explicitly describes a material sample.",
        }],
        "unmatched_raw_information": [], "granularity_mappings": [],
        "proposed_schema_updates": [],
    }


def test_canonical_contract_preserves_mapping():
    graph = CanonicalWorkflowGraph.model_validate(_graph())
    assert graph.nodes[0].object_schema == "MaterialObject"


def test_canonical_object_requires_schema():
    payload = _graph()
    payload["nodes"][0]["object_schema"] = None
    with pytest.raises(ValidationError, match="requires object_schema"):
        CanonicalWorkflowGraph.model_validate(payload)


def test_mapping_must_reference_existing_canonical_node():
    payload = _graph()
    payload["raw_to_canonical_mappings"][0]["canonical_node_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown canonical node"):
        CanonicalWorkflowGraph.model_validate(payload)
