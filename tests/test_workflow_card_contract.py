from mkb.workflows.contract import RAW_WORKFLOW_SCHEMA_VERSION, WorkflowGraph


def test_card_graph_preserves_structured_reproducibility_fields():
    graph = WorkflowGraph.model_validate({
        "schema_version": RAW_WORKFLOW_SCHEMA_VERSION,
        "paper_id": "paper",
        "extraction_id": "extraction",
        "nodes": [{
            "node_id": "n1", "canonical_name": "XRD Measurement",
            "raw_name": "XRD of Material A at 300 K",
            "node_kind": "operation", "node_kind_guess": "operation",
            "semantic_type": "characterization.measurement",
            "parameters": {"temperature": {"value": 300, "unit": "K"}},
            "attributes_explicitly_mentioned": {"temperature": "300 K"},
            "evidence_text": "XRD was measured at 300 K.",
            "paper_location": {"section": "Methods"}, "confidence": 1,
        }],
        "edges": [],
        "reproducibility": {"level": "approximate", "missing_details": ["scan range"]},
    })
    dumped = graph.model_dump(mode="json")
    assert dumped["nodes"][0]["canonical_name"] == "XRD Measurement"
    assert dumped["nodes"][0]["parameters"]["temperature"]["unit"] == "K"
    assert dumped["reproducibility"]["missing_details"] == ["scan range"]


def test_transitional_prompt_fields_are_upgraded_not_discarded():
    graph = WorkflowGraph.model_validate({
        "schema_version": "raw-workflow/1.0", "paper_id": "p", "extraction_id": "e",
        "nodes": [{
            "node_id": "n1", "raw_name": "annealed at 500 C",
            "short_name_guess": "Annealing", "node_kind_guess": "operation",
            "node_category_guess": "thermal processing",
            "parameter_fields": {"temperature": "500 C"},
            "evidence_text": "annealed at 500 C", "paper_location": {}, "confidence": 1,
        }], "edges": [],
    })
    node = graph.nodes[0]
    assert node.canonical_name == "Annealing"
    assert node.parameters == {"temperature": "500 C"}
    assert node.semantic_type == "thermal processing"
