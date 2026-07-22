import pytest
from pydantic import ValidationError

from mkb.workflows.contract import RAW_WORKFLOW_SCHEMA_VERSION, RawWorkflowGraph


def _graph():
    eid = "11111111-1111-1111-1111-111111111111"
    return {
        "schema_version": RAW_WORKFLOW_SCHEMA_VERSION,
        "paper_id": "22222222-2222-2222-2222-222222222222",
        "extraction_id": eid,
        "nodes": [
            {
                "node_id": f"raw:{eid}:n0001", "raw_name": "powder",
                "node_kind_guess": "object", "attributes_explicitly_mentioned": {},
                "evidence_text": "The powder was annealed.",
                "paper_location": {"section": "Methods"}, "confidence": 0.98,
            },
            {
                "node_id": f"raw:{eid}:n0002", "raw_name": "annealed",
                "node_kind_guess": "operation",
                "attributes_explicitly_mentioned": {"temperature": "500 °C"},
                "evidence_text": "The powder was annealed at 500 °C.",
                "paper_location": {"section": "Methods"}, "confidence": 0.96,
            },
        ],
        "edges": [{
            "edge_id": f"raw:{eid}:e0001", "source_node": f"raw:{eid}:n0001",
            "target_node": f"raw:{eid}:n0002", "relation_type": "input_to",
            "evidence_text": "The powder was annealed at 500 °C.", "confidence": 0.95,
        }],
    }


def test_raw_workflow_contract_accepts_evidenced_object_operation_edge():
    graph = RawWorkflowGraph.model_validate(_graph())
    assert graph.edges[0].relation_type == "input_to"


def test_raw_workflow_contract_fills_compatibility_fields_from_canonical_node_shape():
    eid = "11111111-1111-1111-1111-111111111111"
    graph = RawWorkflowGraph.model_validate({
        "schema_version": RAW_WORKFLOW_SCHEMA_VERSION,
        "paper_id": "22222222-2222-2222-2222-222222222222",
        "extraction_id": eid,
        "nodes": [
            {
                "node_id": f"raw:{eid}:n0001",
                "canonical_name": "Precursor Powder",
                "node_kind": "object",
                "evidence_text": "The precursor powder was annealed.",
                "paper_location": {"section": "Methods"},
                "confidence": 0.98,
            },
            {
                "node_id": f"raw:{eid}:n0002",
                "canonical_name": "Annealing",
                "node_kind": "operation",
                "evidence_text": "The precursor powder was annealed.",
                "paper_location": {"section": "Methods"},
                "confidence": 0.96,
            },
        ],
        "edges": [{
            "edge_id": f"raw:{eid}:e0001",
            "source_node": f"raw:{eid}:n0001",
            "target_node": f"raw:{eid}:n0002",
            "relation_type": "input_to",
            "evidence_text": "The precursor powder was annealed.",
            "confidence": 0.95,
        }],
    })

    assert graph.nodes[0].raw_name == "Precursor Powder"
    assert graph.nodes[0].node_kind_guess == "object"


def test_raw_workflow_contract_accepts_multiple_outputs_from_one_operation():
    payload = _graph()
    eid = payload["extraction_id"]
    payload["nodes"].append({
        "node_id": f"raw:{eid}:n0003",
        "raw_name": "crystalline sample A",
        "node_kind_guess": "object",
        "attributes_explicitly_mentioned": {},
        "evidence_text": "The powder was annealed to yield crystalline sample A and exhaust gas.",
        "paper_location": {"section": "Methods"},
        "confidence": 0.97,
    })
    payload["nodes"].append({
        "node_id": f"raw:{eid}:n0004",
        "raw_name": "exhaust gas",
        "node_kind_guess": "object",
        "attributes_explicitly_mentioned": {},
        "evidence_text": "The powder was annealed to yield crystalline sample A and exhaust gas.",
        "paper_location": {"section": "Methods"},
        "confidence": 0.92,
    })
    payload["edges"].append({
        "edge_id": f"raw:{eid}:e0002",
        "source_node": f"raw:{eid}:n0002",
        "target_node": f"raw:{eid}:n0003",
        "relation_type": "produces",
        "evidence_text": "The powder was annealed to yield crystalline sample A and exhaust gas.",
        "confidence": 0.96,
    })
    payload["edges"].append({
        "edge_id": f"raw:{eid}:e0003",
        "source_node": f"raw:{eid}:n0002",
        "target_node": f"raw:{eid}:n0004",
        "relation_type": "produces",
        "evidence_text": "The powder was annealed to yield crystalline sample A and exhaust gas.",
        "confidence": 0.9,
    })

    graph = RawWorkflowGraph.model_validate(payload)

    assert [edge.target_node for edge in graph.edges if edge.relation_type == "produces"] == [
        f"raw:{eid}:n0003",
        f"raw:{eid}:n0004",
    ]


def test_raw_workflow_contract_rejects_unknown_edge_endpoint():
    payload = _graph()
    payload["edges"][0]["target_node"] = "missing"
    with pytest.raises(ValidationError, match="unknown node"):
        RawWorkflowGraph.model_validate(payload)


def test_raw_workflow_contract_rejects_wrong_object_operation_direction():
    payload = _graph()
    payload["edges"][0]["source_node"], payload["edges"][0]["target_node"] = (
        payload["edges"][0]["target_node"], payload["edges"][0]["source_node"]
    )
    with pytest.raises(ValidationError, match="object -> operation"):
        RawWorkflowGraph.model_validate(payload)


def test_raw_workflow_contract_accepts_reasoning_that_motivates_workflow_node():
    payload = _graph()
    eid = payload["extraction_id"]
    payload["nodes"].append({
        "node_id": f"raw:{eid}:n0003",
        "raw_name": "need higher crystallinity before XRD",
        "node_kind_guess": "reasoning",
        "attributes_explicitly_mentioned": {},
        "evidence_text": "To improve crystallinity, the powder was annealed before XRD.",
        "paper_location": {"section": "Methods"},
        "confidence": 0.91,
    })
    payload["edges"].append({
        "edge_id": f"raw:{eid}:e0002",
        "source_node": f"raw:{eid}:n0003",
        "target_node": f"raw:{eid}:n0002",
        "relation_type": "motivates",
        "evidence_text": "To improve crystallinity, the powder was annealed before XRD.",
        "confidence": 0.9,
    })

    graph = RawWorkflowGraph.model_validate(payload)

    assert graph.nodes[-1].node_kind == "reasoning"
    assert graph.edges[-1].relation_type == "motivates"


def test_raw_workflow_contract_rejects_motivates_from_non_reasoning_node():
    payload = _graph()
    payload["edges"][0]["relation_type"] = "motivates"
    with pytest.raises(ValidationError, match="must start from planning or reasoning"):
        RawWorkflowGraph.model_validate(payload)


def test_raw_workflow_contract_rejects_illegal_workflow_endpoint_pair():
    payload = _graph()
    eid = payload["extraction_id"]
    payload["nodes"].append({
        "node_id": f"raw:{eid}:n0003",
        "raw_name": "xrd measurement",
        "node_kind_guess": "operation",
        "attributes_explicitly_mentioned": {},
        "evidence_text": "XRD was performed after annealing.",
        "paper_location": {"section": "Methods"},
        "confidence": 0.95,
    })
    payload["edges"][0]["source_node"] = f"raw:{eid}:n0002"
    payload["edges"][0]["target_node"] = f"raw:{eid}:n0003"
    with pytest.raises(ValidationError, match="illegal workflow edge: operation -> operation"):
        RawWorkflowGraph.model_validate(payload)
