import uuid
from unittest.mock import MagicMock, patch

from mkb.agents.tools.feedback import get_pending_feedback
from mkb.agents.tools.projection import _compact_json_payload
from mkb.agents.tools.reading import read_image_metadata
from mkb.db.models import KnowledgeFrame, Projection


def test_get_pending_feedback_handles_invalid_project_identifier_gracefully():
    result = get_pending_feedback("project_id=not-a-uuid")

    assert result == [{"error": "Invalid project_id: 'project_id=not-a-uuid'"}]


def test_read_image_metadata_handles_invalid_asset_identifier_gracefully():
    result = read_image_metadata("Figure 1B from the AMTN paper")

    assert result == "Invalid asset_id: 'Figure 1B from the AMTN paper'"


def test_request_frame_clarification_appends_well_formed_annotation():
    """request_frame_clarification writes exactly one annotation with expected fields."""
    from mkb.agents.tools.projection import request_frame_clarification

    frame_id = uuid.uuid4()
    project_id = uuid.uuid4()
    projection_id = uuid.uuid4()

    fake_projection = MagicMock(spec=Projection)
    fake_projection.frame_id = frame_id

    fake_frame_read = MagicMock(spec=KnowledgeFrame)
    fake_frame_read.frame_id = frame_id
    fake_frame_read.project_id = project_id

    fake_frame_write = MagicMock(spec=KnowledgeFrame)
    fake_frame_write.agent_annotations = None

    def make_read_session():
        session = MagicMock()

        def query_side_effect(cls):
            q = MagicMock()
            if cls is Projection:
                q.filter_by.return_value.first.return_value = fake_projection
            elif cls is KnowledgeFrame:
                q.filter_by.return_value.first.return_value = fake_frame_read
            return q

        session.query.side_effect = query_side_effect
        return session

    def make_write_session():
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = fake_frame_write
        return session

    read_session = make_read_session()
    write_session = make_write_session()

    call_count = [0]

    def session_factory_side_effect():
        call_count[0] += 1
        session = read_session if call_count[0] == 1 else write_session
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=session)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    clarification_result = {
        "updated": True,
        "clarification_summary": "Added missing synthesis temperature of 800 °C.",
    }

    with patch(
        "mkb.agents.tools.projection.SyncSessionLocal",
        side_effect=session_factory_side_effect,
    ):
        with patch(
            "mkb.agents.clarification.run_clarification_in_thread",
            return_value=clarification_result,
        ):
            result = request_frame_clarification(
                projection_id=str(projection_id),
                question="What is the synthesis temperature?",
                context="Paper mentions a synthesis step.",
                field="synthesis.temperature",
            )

    assert result == clarification_result

    # Verify the annotation was written onto the frame
    annotations = fake_frame_write.agent_annotations
    assert isinstance(annotations, dict)
    assert "clarifications" in annotations
    assert len(annotations["clarifications"]) == 1

    ann = annotations["clarifications"][0]
    assert ann["question"] == "What is the synthesis temperature?"
    assert ann["field"] == "synthesis.temperature"
    assert ann["summary"] == "Added missing synthesis temperature of 800 °C."
    assert ann["frame_updated"] is True
    assert "resolved_at" in ann

    write_session.commit.assert_called_once()


def test_request_frame_clarification_skips_annotation_when_frame_missing():
    """request_frame_clarification does not crash when the frame row is gone on write."""
    from mkb.agents.tools.projection import request_frame_clarification

    frame_id = uuid.uuid4()
    project_id = uuid.uuid4()
    projection_id = uuid.uuid4()

    fake_projection = MagicMock(spec=Projection)
    fake_projection.frame_id = frame_id

    fake_frame_read = MagicMock(spec=KnowledgeFrame)
    fake_frame_read.frame_id = frame_id
    fake_frame_read.project_id = project_id

    def make_read_session():
        session = MagicMock()

        def query_side_effect(cls):
            q = MagicMock()
            if cls is Projection:
                q.filter_by.return_value.first.return_value = fake_projection
            elif cls is KnowledgeFrame:
                q.filter_by.return_value.first.return_value = fake_frame_read
            return q

        session.query.side_effect = query_side_effect
        return session

    def make_write_session_no_frame():
        session = MagicMock()
        # Frame is gone on the write pass
        session.query.return_value.filter_by.return_value.first.return_value = None
        return session

    read_session = make_read_session()
    write_session = make_write_session_no_frame()

    call_count = [0]

    def session_factory_side_effect():
        call_count[0] += 1
        session = read_session if call_count[0] == 1 else write_session
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=session)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    clarification_result = {"updated": False, "clarification_summary": "No change needed."}

    with patch(
        "mkb.agents.tools.projection.SyncSessionLocal",
        side_effect=session_factory_side_effect,
    ):
        with patch(
            "mkb.agents.clarification.run_clarification_in_thread",
            return_value=clarification_result,
        ):
            result = request_frame_clarification(
                projection_id=str(projection_id),
                question="Is there a control group?",
            )

    # Should still return the clarification result despite the missing frame
    assert result == clarification_result
    # commit should NOT have been called (no frame to update)
    write_session.commit.assert_not_called()


def test_compact_json_payload_trims_large_strings_lists_and_dicts():
    payload = {
        f"key_{i}": {
            "text": "x" * 5000,
            "items": list(range(200)),
        }
        for i in range(200)
    }

    compacted, truncated = _compact_json_payload(
        payload,
        max_chars=20000,
        max_list_items=20,
        max_dict_items=25,
        max_string_chars=120,
    )

    assert truncated is True
    assert isinstance(compacted, dict)
    assert len(compacted) <= 25
    first_value = next(iter(compacted.values()))
    assert len(first_value["items"]) <= 20
    assert len(first_value["text"]) <= 120
