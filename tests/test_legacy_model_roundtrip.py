import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from mkb import CollectionGroup, FeedbackItem, PostProcessor, Projection, Record, Skill, WorkflowRecord
from mkb.adapters.repositories import (
    SQLAlchemyCollectionGroupRepository,
    SQLAlchemyFeedbackRepository,
    SQLAlchemyPostProcessorRepository,
    SQLAlchemyProjectionRepository,
    SQLAlchemyRecordRepository,
    SQLAlchemySkillRepository,
    SQLAlchemyWorkflowRepository,
)


def _round_trip(model):
    serialized = model.model_dump(mode="json")
    restored = type(model).model_validate(serialized)
    assert restored == model
    return serialized


def test_legacy_record_payload_round_trip_preserves_evidence_and_annotations():
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        frame_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status="COMPLETED",
        content={
            "materials": [
                {
                    "name": "calcite",
                    "evidence_level": 2,
                    "source_evidence": {"page": 4, "text": "CaCO3 was observed."},
                }
            ]
        },
        extraction_summary="one supported material",
        times_checked=3,
        extraction_version=5,
        extracted_at=now,
        source_metadata={"asset_ids": [str(uuid.uuid4())], "schema": "frame-v2"},
        agent_annotations={"review": {"status": "accepted", "notes": ["retain"]}},
        created_at=now,
        updated_at=now,
    )

    model = SQLAlchemyRecordRepository._model(row)
    serialized = _round_trip(model)

    assert isinstance(model, Record)
    assert serialized["data"] == row.content
    assert serialized["source_metadata"] == row.source_metadata
    assert serialized["annotations"] == row.agent_annotations


def test_legacy_projection_round_trip_preserves_review_and_schema_version():
    now = datetime.now(timezone.utc)
    collection_id = uuid.uuid4()
    row = SimpleNamespace(
        projection_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        frame_id=uuid.uuid4(),
        source_type="frame",
        status="COMPLETED",
        data={"result": {"value": 12.5, "evidence": ["table-2"]}},
        validation_result={"valid": True, "warnings": []},
        agent_notes="kept exactly",
        extracted_at=now,
        space_version=7,
        times_reviewed=2,
        review_notes="verified against table",
        reviewed_at=now,
        deleted_at=None,
        superseded_by_id=None,
        supersedes_ids=[str(uuid.uuid4())],
        created_at=now,
        updated_at=now,
    )

    model = SQLAlchemyProjectionRepository._model(row, collection_id)
    serialized = _round_trip(model)

    assert isinstance(model, Projection)
    assert serialized["data"] == row.data
    assert serialized["schema_version"] == 7
    assert serialized["review_notes"] == row.review_notes


def test_legacy_raw_workflow_round_trip_is_lossless():
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        extraction_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        version=4,
        schema_version="workflow-2",
        extractor_version="extractor-2026.07",
        model="provider/model",
        status="COMPLETED",
        record_status="active",
        supersedes_extraction_id=uuid.uuid4(),
        correction_reason="source clarification",
        correction_author="reviewer",
        correction_details={"affected_nodes": ["n1"], "evidence": "methods"},
        review_flags=[{"type": "ambiguous", "node_id": "n2"}],
        graph={
            "nodes": [{"id": "n1", "evidence_text": "heated at 800 C"}],
            "edges": [{"source": "n1", "target": "n2", "confidence": 0.91}],
        },
        checkpoint={"stage": "validated", "completed_nodes": ["n1"]},
        provenance={"source_asset_ids": [str(uuid.uuid4())], "prompt_version": "3"},
        error=None,
        extracted_at=now,
        checkpoint_updated_at=now,
        created_at=now,
    )

    model = SQLAlchemyWorkflowRepository._model(row)
    serialized = _round_trip(model)

    assert isinstance(model, WorkflowRecord)
    assert serialized["id"] == str(row.extraction_id)
    assert serialized["graph"] == row.graph
    assert serialized["checkpoint"] == row.checkpoint
    assert serialized["provenance"] == row.provenance
    assert serialized["review_flags"] == row.review_flags


def test_legacy_group_feedback_and_skill_mapping_preserves_ids_and_content():
    now = datetime.now(timezone.utc)
    group_row = SimpleNamespace(
        group_id=uuid.uuid4(),
        name="Existing group",
        description="Preserved",
        color="#112233",
        display_order=4,
        created_at=now,
        updated_at=now,
    )
    feedback_row = SimpleNamespace(
        feedback_id=uuid.uuid4(),
        target_frame_id=uuid.uuid4(),
        target_project_id=uuid.uuid4(),
        category="ambiguous_data",
        question="Which unit?",
        source_agent="projection-reviewer",
        source_projection_id=uuid.uuid4(),
        field_path="temperature",
        context="table 2",
        status="ACKNOWLEDGED",
        resolution_notes=None,
        resolved_by=None,
        resolved_at=None,
        created_at=now,
        updated_at=now,
    )
    skill_row = SimpleNamespace(
        skill_id=uuid.uuid4(),
        name="Existing skill",
        slug="existing-skill",
        skill_md="# Existing\nKeep this content.",
        description="Preserved",
        metadata_={"origin": "legacy"},
        created_at=now,
        updated_at=now,
    )

    group = SQLAlchemyCollectionGroupRepository._model(group_row, 3)
    feedback = SQLAlchemyFeedbackRepository._model(feedback_row)
    skill = SQLAlchemySkillRepository._model(skill_row)

    assert isinstance(group, CollectionGroup)
    assert group.id == group_row.group_id
    assert group.collection_count == 3
    assert isinstance(feedback, FeedbackItem)
    assert feedback.status == "IN_REVIEW"
    assert feedback.id == feedback_row.feedback_id
    assert isinstance(skill, Skill)
    assert skill.content == skill_row.skill_md


def test_legacy_post_processor_mapping_reads_existing_source(tmp_path):
    now = datetime.now(timezone.utc)
    path = tmp_path / "processor.py"
    path.write_text("print('{}')\n", encoding="utf-8")
    row = SimpleNamespace(
        script_id=uuid.uuid4(),
        name="Existing processor",
        filename="processor.py",
        storage_path=str(path),
        created_at=now,
    )

    processor = SQLAlchemyPostProcessorRepository._model(row)

    assert isinstance(processor, PostProcessor)
    assert processor.id == row.script_id
    assert processor.source == "print('{}')\n"
    assert processor.metadata == {"content_missing": False}
