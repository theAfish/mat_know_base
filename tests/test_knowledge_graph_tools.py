from mkb.agents.tools.knowledge_graph import normalize_knowledge_graph_payload


def test_normalize_knowledge_graph_payload_enforces_concept_only_shape():
    payload, validation = normalize_knowledge_graph_payload(
        {
            "concepts": [
                {
                    "label": "Amelotin",
                    "aliases": ["AMTN", "AMTN"],
                    "properties": {"mw": 22000},
                    "source_project_id": "project-1",
                    "knowledge_refs": [{"field_path": "materials[0]", "snippet": "Amelotin-rich matrix"}],
                }
            ],
            "relations": [
                {
                    "source": "Amelotin",
                    "relation": "promotes",
                    "target": "Hydroxyapatite nucleation",
                    "evidence_level": "2",
                    "units": "%",
                    "knowledge_ref": {"field_path": "results[2]", "snippet": "nucleation increased"},
                }
            ],
        }
    )

    assert validation["concept_count"] == 2
    assert payload["concepts"][0].keys() == {
        "label",
        "aliases",
        "source_project_ids",
        "source_frame_ids",
        "knowledge_refs",
    }
    assert payload["relations"][0].keys() == {
        "source",
        "relation",
        "target",
        "evidence_level",
        "knowledge_ref",
    }


def test_normalize_knowledge_graph_payload_dedupes_concepts_and_relations():
    payload, validation = normalize_knowledge_graph_payload(
        {
            "nodes": [
                {"label": "Amelotin"},
                {"label": " amelotin "},
            ],
            "edges": [
                {"source": "Amelotin", "predicate": "promotes", "target": "Hydroxyapatite"},
                {"source": "amelotin", "relation": "promotes", "target": "hydroxyapatite", "evidence_level": 1},
            ],
        }
    )

    assert len(payload["concepts"]) == 2
    assert len(payload["relations"]) == 1
    assert payload["relations"][0]["evidence_level"] == 1
    assert validation["duplicate_relation_count"] == 1
