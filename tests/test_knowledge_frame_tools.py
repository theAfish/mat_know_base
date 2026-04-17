from mkb.agents.tools import (
    _default_frame_data,
    _normalize_evidence_level,
    add_knowledge_frame_items,
)


def test_normalize_evidence_level_variants():
    assert _normalize_evidence_level(1).startswith("Level 1:")
    assert _normalize_evidence_level("level 2").startswith("Level 2:")
    assert _normalize_evidence_level("correlative").startswith("Level 3:")
    assert _normalize_evidence_level("predicted by model").startswith("Level 4:")


def test_default_frame_data_sections():
    data = _default_frame_data()
    assert set(data.keys()) == {"concepts", "experimental_data", "statements", "related_data"}
    assert all(isinstance(v, list) for v in data.values())


def test_add_knowledge_frame_items_rejects_invalid_section():
    result = add_knowledge_frame_items(
        batch_id="00000000-0000-0000-0000-000000000000",
        section="invalid_section",
        items=[{"name": "x"}],
    )
    assert result["status"] == "error"
