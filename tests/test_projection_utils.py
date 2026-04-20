from mkb.agents.tools.projection import _inject_source_project_references
from mkb.ui.pages.projections import (
    _build_projection_section_rows,
    _filter_latest_projections,
    _mapping_to_rows,
    _paginate_table_rows,
    _projection_to_section_rows,
)


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


def test_projection_to_section_rows_keeps_sections_separate_and_preserves_history_metadata():
    rows = _projection_to_section_rows(
        {
            "projection_id": "projection-1",
            "project_id": "project-1",
            "frame_id": "frame-1",
            "status": "COMPLETED",
            "space_version": 2,
            "extracted_at": "2026-04-20T00:00:00Z",
            "data": {
                "templates": [
                    {"name": "Amelogenin", "functional_tags": ["nucleation", "growth"]},
                    {"name": "ODAM", "evidence_level": 2},
                ],
                "summary": {"confidence": "high"},
            },
        }
    )

    assert rows == {
        "templates": [
            {
                "project_id": "project-1",
                "projection_id": "projection-1",
                "extracted_at": "2026-04-20T00:00:00Z",
                "name": "Amelogenin",
                "functional_tags": "nucleation, growth",
            },
            {
                "project_id": "project-1",
                "projection_id": "projection-1",
                "extracted_at": "2026-04-20T00:00:00Z",
                "name": "ODAM",
                "evidence_level": "2",
            },
        ],
        "summary": [
            {
                "project_id": "project-1",
                "projection_id": "projection-1",
                "extracted_at": "2026-04-20T00:00:00Z",
                "confidence": "high",
            }
        ],
    }


def test_build_projection_section_rows_combines_same_named_sections_across_projections():
    rows = _build_projection_section_rows(
        [
            {
                "projection_id": "projection-1",
                "project_id": "project-1",
                "status": "COMPLETED",
                "extracted_at": "2026-04-20T00:00:00Z",
                "data": {"templates": [{"name": "A"}]},
            },
            {
                "projection_id": "projection-2",
                "project_id": "project-2",
                "status": "COMPLETED",
                "extracted_at": "2026-04-20T01:00:00Z",
                "data": {"templates": [{"name": "B"}], "summary": {"confidence": "medium"}},
            },
        ]
    )

    assert rows == {
        "templates": [
            {"project_id": "project-1", "projection_id": "projection-1", "extracted_at": "2026-04-20T00:00:00Z", "name": "A"},
            {"project_id": "project-2", "projection_id": "projection-2", "extracted_at": "2026-04-20T01:00:00Z", "name": "B"},
        ],
        "summary": [
            {"project_id": "project-2", "projection_id": "projection-2", "extracted_at": "2026-04-20T01:00:00Z", "confidence": "medium"},
        ],
    }


def test_filter_latest_projections_keeps_only_newest_projection_per_project_and_space():
    filtered = _filter_latest_projections(
        [
            {
                "projection_id": "old-proj",
                "project_id": "project-1",
                "frame_id": "frame-1",
                "space_id": "space-1",
                "status": "COMPLETED",
                "extracted_at": "2026-04-20T00:00:00Z",
            },
            {
                "projection_id": "new-proj",
                "project_id": "project-1",
                "frame_id": "frame-1",
                "space_id": "space-1",
                "status": "COMPLETED",
                "extracted_at": "2026-04-20T01:00:00Z",
            },
            {
                "projection_id": "other-proj",
                "project_id": "project-2",
                "frame_id": "frame-2",
                "space_id": "space-1",
                "status": "COMPLETED",
                "extracted_at": "2026-04-20T00:30:00Z",
            },
        ]
    )

    assert [projection["projection_id"] for projection in filtered] == ["new-proj", "other-proj"]


def test_paginate_table_rows_limits_output_to_requested_page():
    rows = [{"row": str(index)} for index in range(1, 121)]

    page_rows, total_pages = _paginate_table_rows(rows, page_size=50, page_number=3)

    assert total_pages == 3
    assert page_rows == [{"row": str(index)} for index in range(101, 121)]
