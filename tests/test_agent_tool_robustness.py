import json
import uuid
from unittest.mock import MagicMock, patch

from mkb.agents.runtime import AgentRuntime
from mkb.agents.tools.feedback import get_pending_feedback
from mkb.agents.tools.projection import _compact_json_payload
from mkb.agents.tools.reading import read_image_metadata
from mkb.db.models import KnowledgeFrame, Projection


def test_get_pending_feedback_handles_invalid_project_identifier_gracefully():
    from mkb.agents.runtime import AgentRuntime

    result = get_pending_feedback(
        "project_id=not-a-uuid",
        runtime=AgentRuntime(database=object()),
    )

    assert result == [{"error": "Invalid project_id: 'project_id=not-a-uuid'"}]


def test_read_image_metadata_handles_invalid_asset_identifier_gracefully():
    from mkb.agents.runtime import AgentRuntime

    session = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = session
    context.__exit__.return_value = False
    result = read_image_metadata(
        "Figure 1B from the AMTN paper",
        runtime=AgentRuntime(database=MagicMock(session=lambda: context)),
    )

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

    runtime = AgentRuntime(database=MagicMock(session=session_factory_side_effect))
    with patch(
        "mkb.agents.clarification.run_clarification_in_thread",
        return_value=clarification_result,
    ):
        result = request_frame_clarification(
            projection_id=str(projection_id),
            question="What is the synthesis temperature?",
            context="Paper mentions a synthesis step.",
            field="synthesis.temperature",
            runtime=runtime,
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

    runtime = AgentRuntime(database=MagicMock(session=session_factory_side_effect))
    with patch(
        "mkb.agents.clarification.run_clarification_in_thread",
        return_value=clarification_result,
    ):
        result = request_frame_clarification(
            projection_id=str(projection_id),
            question="Is there a control group?",
            runtime=runtime,
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


# ---------------------------------------------------------------------------
# update_projection tests
# ---------------------------------------------------------------------------


def _make_update_projection_session(fake_projection, fake_frame):
    """Return a session context manager for an injected database adapter."""

    def query_side_effect(cls):
        q = MagicMock()
        if cls is Projection:
            q.filter_by.return_value.first.return_value = fake_projection
        elif cls is KnowledgeFrame:
            q.filter_by.return_value.first.return_value = fake_frame
        return q

    session = MagicMock()
    session.query.side_effect = query_side_effect
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    return cm, session


def test_update_projection_parses_json_string_inputs():
    """Additions and removals passed as JSON strings are parsed before use."""
    from mkb.agents.tools.projection import update_projection

    projection_id = uuid.uuid4()
    frame_id = uuid.uuid4()

    fake_projection = MagicMock(spec=Projection)
    fake_projection.projection_id = projection_id
    fake_projection.frame_id = frame_id
    fake_projection.data = {"records": [{"name": "existing"}]}
    fake_projection.status.value = "COMPLETED"
    fake_projection.agent_notes = ""

    fake_frame = MagicMock(spec=KnowledgeFrame)
    fake_frame.project_id = uuid.uuid4()

    cm, session = _make_update_projection_session(fake_projection, fake_frame)

    additions_str = json.dumps({"records": [{"name": "new_item"}]})

    with patch("mkb.agents.tools.projection.write_projection_trace"):
        result = update_projection(
            projection_id=str(projection_id),
            additions=additions_str,
            runtime=AgentRuntime(database=MagicMock(session=lambda: cm)),
        )

    assert result["changes_made"]["additions"] == 1
    assert result["changes_made"]["removals"] == 0
    session.commit.assert_called_once()


def test_update_projection_additions_append_to_existing_list():
    """Additions extend an existing list in the projection data."""
    from mkb.agents.tools.projection import update_projection

    projection_id = uuid.uuid4()
    frame_id = uuid.uuid4()

    fake_projection = MagicMock(spec=Projection)
    fake_projection.projection_id = projection_id
    fake_projection.frame_id = frame_id
    fake_projection.data = {"records": [{"name": "alpha"}]}
    fake_projection.status.value = "COMPLETED"
    fake_projection.agent_notes = ""

    fake_frame = None  # no frame — source_project_id injection skipped

    cm, session = _make_update_projection_session(fake_projection, fake_frame)

    with patch("mkb.agents.tools.projection.write_projection_trace"):
        update_projection(
            projection_id=str(projection_id),
            additions={"records": [{"name": "beta"}, {"name": "gamma"}]},
            runtime=AgentRuntime(database=MagicMock(session=lambda: cm)),
        )

    updated_data = fake_projection.data
    assert len(updated_data["records"]) == 3
    assert updated_data["records"][0]["name"] == "alpha"
    assert updated_data["records"][1]["name"] == "beta"
    assert updated_data["records"][2]["name"] == "gamma"


def test_update_projection_removals_applied_in_descending_index_order():
    """Removals at higher indices are processed first to avoid index shifting."""
    from mkb.agents.tools.projection import update_projection

    projection_id = uuid.uuid4()
    frame_id = uuid.uuid4()

    fake_projection = MagicMock(spec=Projection)
    fake_projection.projection_id = projection_id
    fake_projection.frame_id = frame_id
    fake_projection.data = {"records": [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]}
    fake_projection.status.value = "COMPLETED"
    fake_projection.agent_notes = ""

    fake_frame = None

    cm, session = _make_update_projection_session(fake_projection, fake_frame)

    # Remove indices 1 and 3 (b and d). Providing in ascending order to verify
    # the function itself reorders them descending.
    removals = [
        {"key": "records", "index": 1, "reason": "duplicate"},
        {"key": "records", "index": 3, "reason": "irrelevant"},
    ]

    with patch("mkb.agents.tools.projection.write_projection_trace"):
        result = update_projection(
            projection_id=str(projection_id),
            removals=removals,
            runtime=AgentRuntime(database=MagicMock(session=lambda: cm)),
        )

    assert result["changes_made"]["removals"] == 2
    remaining_names = [r["name"] for r in fake_projection.data["records"]]
    assert remaining_names == ["a", "c"]


def test_update_projection_injects_source_project_id_when_frame_exists():
    """When a frame is found, source_project_id is injected into added items."""
    from mkb.agents.tools.projection import update_projection

    projection_id = uuid.uuid4()
    frame_id = uuid.uuid4()
    project_id = uuid.uuid4()

    fake_projection = MagicMock(spec=Projection)
    fake_projection.projection_id = projection_id
    fake_projection.frame_id = frame_id
    fake_projection.data = {}
    fake_projection.status.value = "IN_PROGRESS"
    fake_projection.agent_notes = ""

    fake_frame = MagicMock(spec=KnowledgeFrame)
    fake_frame.project_id = project_id

    cm, session = _make_update_projection_session(fake_projection, fake_frame)

    with patch("mkb.agents.tools.projection.write_projection_trace"):
        update_projection(
            projection_id=str(projection_id),
            additions={"records": [{"name": "item_a"}]},
            runtime=AgentRuntime(database=MagicMock(session=lambda: cm)),
        )

    records = fake_projection.data["records"]
    assert len(records) == 1
    assert records[0]["source_project_id"] == str(project_id)
