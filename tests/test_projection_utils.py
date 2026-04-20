from mkb.agents.tools.projection import _inject_source_project_references
from mkb.ui.pages.projections import _mapping_to_rows


def test_inject_source_project_references_sets_project_id_on_records():
    data = {
        "templates": [
            {"template_name": "Amelotin", "references": "doi:old"},
            {"template_name": "ODAM"},
        ],
        "overall_assessment": {
            "confidence": "high",
            "summary": "Strong support for biomineralization activity.",
        },
    }

    enriched = _inject_source_project_references(data, "project-123")

    assert [row["references"] for row in enriched["templates"]] == ["project-123", "project-123"]
    assert enriched["overall_assessment"]["confidence"] == "high"


def test_mapping_to_rows_formats_nested_values_for_table_display():
    rows = _mapping_to_rows(
        {
            "confidence": "high",
            "evidence_count": 3,
            "highlights": ["nucleation", "orientation"],
        }
    )

    assert rows == [
        {"Field": "confidence", "Value": "high"},
        {"Field": "evidence_count", "Value": "3"},
        {"Field": "highlights", "Value": "nucleation, orientation"},
    ]
