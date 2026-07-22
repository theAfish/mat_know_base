import uuid

from mkb.workflows.review import audit_raw_graph


def test_audit_flags_node_only_workflow_graph():
    eid = str(uuid.uuid4())
    graph = {
        "schema_version": "workflow-cards/2.0",
        "paper_id": str(uuid.uuid4()),
        "extraction_id": eid,
        "nodes": [
            {
                "node_id": f"raw:{eid}:n0001",
                "raw_name": "input structure",
                "node_kind": "object",
                "node_kind_guess": "object",
                "evidence_text": "input structure",
                "paper_location": {},
                "confidence": 0.9,
            },
            {
                "node_id": f"raw:{eid}:n0002",
                "raw_name": "DFT calculation",
                "node_kind": "operation",
                "node_kind_guess": "operation",
                "evidence_text": "DFT calculation",
                "paper_location": {},
                "confidence": 0.9,
            },
        ],
        "edges": [],
    }

    flags = audit_raw_graph(graph)

    assert {
        "type": "missing_workflow_edges",
        "item_type": "graph",
        "item_id": eid,
    } in flags
