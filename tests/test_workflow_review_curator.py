import json
import uuid

import pytest
from pydantic import ValidationError

from mkb.workflows.curator import (
    apply_proposal, validate_proposal,
)
from mkb.workflows import editing as workflow_editing
from mkb.workflows.review import audit_raw_graph, rebase_graph
from mkb.workflows.contract import RawWorkflowGraph
from mkb.workflows.validation import json_safe_validation_errors
from mkb.agents.tools import workflows as workflow_tools


def _raw_graph():
    eid = uuid.uuid4()
    return {
        "schema_version": "raw-workflow/1.0", "paper_id": str(uuid.uuid4()),
        "extraction_id": str(eid),
        "nodes": [
            {"node_id": f"raw:{eid}:n0001", "raw_name": "sample", "node_kind_guess": "object", "evidence_text": "sample", "paper_location": {}, "confidence": 0.4},
            {"node_id": f"raw:{eid}:n0002", "raw_name": "anneal", "node_kind_guess": "operation", "evidence_text": "annealed", "paper_location": {}, "confidence": 1.0},
        ],
        "edges": [{"edge_id": f"raw:{eid}:e0001", "source_node": f"raw:{eid}:n0001", "target_node": f"raw:{eid}:n0002", "relation_type": "input_to", "evidence_text": "annealed", "confidence": 1.0}],
    }


def test_audit_and_rebase_correction_graph():
    graph = _raw_graph()
    assert any(flag["type"] == "low_confidence" for flag in audit_raw_graph(graph))
    new_id = uuid.uuid4()
    corrected = rebase_graph(graph, new_id)
    assert corrected["extraction_id"] == str(new_id)
    assert corrected["nodes"][0]["node_id"].startswith(f"raw:{new_id}:n")


def test_audit_allows_planning_edge_to_downstream_operation():
    graph = _raw_graph()
    eid = graph["extraction_id"]
    graph["nodes"].append({
        "node_id": f"raw:{eid}:n0003",
        "raw_name": "screen high temperature phase stability",
        "node_kind_guess": "planning",
        "evidence_text": "We screened high temperature phase stability before annealing.",
        "paper_location": {},
        "confidence": 0.9,
    })
    graph["edges"].append({
        "edge_id": f"raw:{eid}:e0002",
        "source_node": f"raw:{eid}:n0003",
        "target_node": f"raw:{eid}:n0002",
        "relation_type": "leads_to",
        "evidence_text": "We screened high temperature phase stability before annealing.",
        "confidence": 0.9,
    })

    flags = audit_raw_graph(graph)

    assert not [flag for flag in flags if flag["type"] == "impossible_edge"]


def test_validation_errors_are_safe_for_agent_request_serialization():
    graph = _raw_graph()
    graph["edges"][0]["relation_type"] = "produces"
    with pytest.raises(ValidationError) as captured:
        RawWorkflowGraph.model_validate(graph)
    errors = json_safe_validation_errors(captured.value)
    json.dumps(errors)
    assert all("ctx" not in error for error in errors)


def test_schema_proposal_requires_evidence_and_applies_immutably():
    library = {"schema_version": "workflow-schema/1.1", "operation_templates": {}}
    assert validate_proposal("create_template", {"slug": "mix", "label": "Mix"}, [], library)
    updated = apply_proposal(library, "create_template", {"slug": "mix", "label": "Mix"})
    assert library["operation_templates"] == {}
    assert "operation-template:workflow-schema/1.1:mix" in updated["operation_templates"]


def test_template_accepts_open_ended_parameter_keys():
    library = {"schema_version": "workflow-schema/1.1", "operation_templates": {}}
    payload = {
        "slug": "born-effective-charge-tensors-calculation",
        "label": "Born effective charge tensors calculation",
        "parameters": {
            "method": "dft",
            "response_tensor": "born_effective_charge",
            "boundary_condition": "periodic",
        },
        "aliases": ["DFT calculation of Born effective charge tensors"],
        "slots": [
            {"key": "exchange_correlation_functional", "type": "string"},
            {"key": "k_point_mesh", "type": "integer_tuple"},
        ],
    }
    assert not validate_proposal("create_template", payload, ["workflow-id"], library)
    updated = apply_proposal(library, "create_template", payload)
    template = updated["operation_templates"][
        "operation-template:workflow-schema/1.1:born-effective-charge-tensors-calculation"
    ]
    assert template["parameters"]["method"] == "dft"
    assert template["parameters"]["response_tensor"] == "born_effective_charge"
    assert template["slots"][0]["key"] == "exchange_correlation_functional"
    assert template["label"] == "Born effective charge tensors calculation"


def test_parameter_keys_must_be_valid_but_are_not_hard_coded():
    errors = validate_proposal(
        "create_template",
        {
            "slug": "custom-operation",
            "label": "Custom operation",
            "parameters": {"": "invalid", "novel_domain_key": 42},
        },
        ["workflow-id"],
        {"operation_templates": {}},
    )
    assert any("parameter keys" in error for error in errors)


def test_add_slot_supports_agent_created_structured_keys():
    template_id = "operation-template:workflow-schema/1.0:calculation"
    library = {
        "schema_version": "workflow-schema/1.1",
        "operation_templates": {
            template_id: {"label": "Calculation", "aliases": [], "slots": []},
        },
    }
    updated = apply_proposal(library, "add_slot", {
        "template_id": template_id,
        "slot": {
            "key": "method",
            "type": "string",
            "description": "Computational method used for this calculation",
        },
    })
    assert updated["operation_templates"][template_id]["slots"][0]["key"] == "method"


def test_object_and_operation_cards_evolve_symmetrically():
    library = {"schema_version": "workflow-schema/2.0", "cards": {}}
    payload = {
        "slug": "xrd-spectrum", "canonical_name": "XRD Spectrum",
        "kind": "object", "aliases": ["X-ray diffraction pattern"],
    }
    assert not validate_proposal("create_card", payload, ["workflow-id"], library)
    updated = apply_proposal(library, "create_card", payload)
    card_id = "card:workflow-schema/2.0:object:xrd-spectrum"
    assert updated["cards"][card_id]["kind"] == "object"
    assert updated["cards"][card_id]["canonical_name"] == "XRD Spectrum"


def test_extractor_card_search_uses_newest_library(monkeypatch):
    monkeypatch.setattr(
        workflow_editing,
        "get_schema_library_payload",
        lambda: {
            "schema_version": "workflow-schema/9.9",
            "cards": {
                "card:workflow-schema/9.9:operation:annealing": {
                    "canonical_name": "Annealing",
                    "kind": "operation",
                    "aliases": ["heat treatment"],
                    "parameter_slots": [{"key": "temperature"}],
                    "status": "active",
                },
            },
            "operation_templates": {
                "operation-template:workflow-schema/9.9:annealing": {
                    "label": "Annealing",
                    "aliases": ["thermal anneal"],
                    "slots": [{"key": "duration"}],
                    "parameters": {"method": "thermal"},
                },
            },
        },
    )

    result = workflow_tools.search_workflow_cards("annealing", node_kind="operation")

    assert result["schema_version"] == "workflow-schema/9.9"
    assert result["results"][0]["match_type"] in {"card", "operation_template"}
    assert any(item.get("canonical_name") == "Annealing" or item.get("label") == "Annealing" for item in result["results"])
