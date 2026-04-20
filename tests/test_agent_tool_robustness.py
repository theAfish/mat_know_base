from mkb.agents.tools.feedback import get_pending_feedback
from mkb.agents.tools.reading import read_image_metadata


def test_get_pending_feedback_handles_invalid_project_identifier_gracefully():
    result = get_pending_feedback("project_id=not-a-uuid")

    assert result == [{"error": "Invalid project_id: 'project_id=not-a-uuid'"}]


def test_read_image_metadata_handles_invalid_asset_identifier_gracefully():
    result = read_image_metadata("Figure 1B from the AMTN paper")

    assert result == "Invalid asset_id: 'Figure 1B from the AMTN paper'"
