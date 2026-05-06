from mkb.agents.tools import knowledge_graph as kg_tools
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


def test_summarize_graph_for_agent_truncates_and_surfaces_top_connected():
    graph = {
        "concepts": [
            {
                "label": "Amelotin",
                "aliases": ["AMTN"],
                "source_project_ids": ["p1"],
                "source_frame_ids": ["f1"],
                "knowledge_refs": [{"snippet": "Amelotin-rich matrix"}],
            },
            {"label": "Hydroxyapatite", "aliases": [], "source_project_ids": [], "source_frame_ids": [], "knowledge_refs": []},
            {"label": "Enamel", "aliases": [], "source_project_ids": [], "source_frame_ids": [], "knowledge_refs": []},
        ],
        "relations": [
            {"source": "Amelotin", "relation": "promotes", "target": "Hydroxyapatite", "evidence_level": 2},
            {"source": "Amelotin", "relation": "present_in", "target": "Enamel", "evidence_level": 3},
        ],
    }

    summary = kg_tools._summarize_graph_for_agent(graph, concept_limit=2, relation_limit=1, top_connected_limit=2)

    assert summary["response_mode"] == "summary"
    assert summary["truncated"] is True
    assert summary["graph_summary"]["concept_count"] == 3
    assert summary["graph_summary"]["relation_count"] == 2
    assert summary["graph_summary"]["top_connected_concepts"][0]["label"] == "Amelotin"
    assert summary["graph_summary"]["top_connected_concepts"][0]["degree"] == 2
    assert summary["graph"]["concepts"] == [
        {"label": "Amelotin", "aliases": ["AMTN"]},
        {"label": "Enamel"},
    ]
    assert summary["graph"]["relations"] == [
        {"source": "Amelotin", "relation": "present_in", "target": "Enamel", "evidence_level": 3}
    ]


def test_search_graph_elements_uses_full_graph_snapshot(monkeypatch):
    calls = []

    def fake_snapshot(space_id, exclude_projection_id=None, full_graph=False, **kwargs):
        calls.append({"space_id": space_id, "full_graph": full_graph})
        return {
            "graph": {
                "concepts": [
                    {"label": "Amelotin", "aliases": ["AMTN"]},
                    {"label": "Hydroxyapatite", "aliases": ["HAp"]},
                ],
                "relations": [
                    {
                        "source": "Amelotin",
                        "relation": "promotes",
                        "target": "Hydroxyapatite",
                        "evidence_level": 2,
                    }
                ],
            }
        }

    monkeypatch.setattr(kg_tools, "get_current_graph_snapshot", fake_snapshot)

    result = kg_tools.search_graph_elements("space-1", ["AMTN", "hydroxy"], limit=5)

    assert calls == [{"space_id": "space-1", "full_graph": True}]
    assert result["normalized_terms"] == ["amtn", "hydroxy"]
    assert result["concept_count"] == 2
    assert result["relation_count"] == 1
    assert result["concepts"][0]["label"] == "Amelotin"
    assert result["relations"][0]["target"] == "Hydroxyapatite"
