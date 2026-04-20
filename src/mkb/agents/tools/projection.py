"""
Projection tools for the projection agent.

These tools let the projection agent read knowledge frame content,
save projection results, and flag data for feedback.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    Feedback,
    FeedbackStatus,
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
    Space,
)
from mkb.spaces.schema_utils import normalize_projection_data

logger = logging.getLogger(__name__)


def _inject_source_project_references(data, source_project_id: str):
    """Recursively attach source-project metadata to extracted records."""
    if isinstance(data, list):
        return [
            _inject_source_project_references(item, source_project_id)
            for item in data
        ]

    if isinstance(data, dict):
        enriched = {
            key: _inject_source_project_references(value, source_project_id)
            for key, value in data.items()
        }

        lower_keys = {str(key).lower() for key in enriched}
        scalar_fields = [
            key for key, value in enriched.items()
            if not isinstance(value, (dict, list))
        ]
        if scalar_fields and "source_project_id" not in lower_keys:
            enriched["source_project_id"] = source_project_id

        return enriched

    return data


def get_frame_content(frame_id: str) -> dict:
    """Read the knowledge frame content for projection.

    Returns the full frame content dict, or an error if not found.
    """
    fid = parse_uuidish(frame_id)
    if not fid:
        return {"error": invalid_identifier_message("frame_id", frame_id)}

    with SyncSessionLocal() as session:
        frame = session.query(KnowledgeFrame).filter_by(frame_id=fid).first()
        if not frame:
            return {"error": f"Frame {frame_id} not found."}
        return {
            "frame_id": str(frame.frame_id),
            "project_id": str(frame.project_id),
            "status": frame.status.value,
            "content": frame.content or {},
            "extraction_summary": frame.extraction_summary,
        }


def save_projection(
    projection_id: str,
    data: dict,
    validation_notes: str = "",
    agent_notes: str = "",
) -> dict:
    """Save extracted projection data.

    Args:
        projection_id: The projection record to update.
        data: The structured data extracted per the space schema.
        validation_notes: Notes about data validation.
        agent_notes: Agent's confidence assessment and observations.

    Returns:
        Dict with projection_id and status.
    """
    pid = parse_uuidish(projection_id)
    if not pid:
        return {"error": invalid_identifier_message("projection_id", projection_id)}

    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            return {"error": f"Projection {projection_id} not found."}

        frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
        space = session.query(Space).filter_by(space_id=projection.space_id).first()

        normalized_data, validation_result = normalize_projection_data(
            data,
            space.extraction_schema if space else {},
        )

        source_project_id = str(frame.project_id) if frame else None
        if source_project_id:
            normalized_data = _inject_source_project_references(normalized_data, source_project_id)

        if validation_notes:
            validation_result = {
                **validation_result,
                "notes": validation_notes,
            }

        projection.data = normalized_data
        projection.validation_result = validation_result or None
        projection.agent_notes = agent_notes
        projection.status = ProjectionStatus.COMPLETED
        projection.extracted_at = now
        session.commit()

        return {"projection_id": str(projection.projection_id), "status": "completed"}


def flag_for_feedback(
    projection_id: str,
    field: str,
    issue: str,
    question: str,
    context: str = "",
) -> dict:
    """Flag unclear or ambiguous data for feedback to the KB extraction agent.

    Args:
        projection_id: The projection encountering the issue.
        field: The field path where the issue was found (e.g., "catalysts[2].selectivity").
        issue: Category of the issue (missing_data, ambiguous_data, inconsistency, wrong_evidence_level, other).
        question: The specific question or clarification needed.
        context: Relevant excerpt from the knowledge frame.

    Returns:
        Dict with feedback_id.
    """
    pid = parse_uuidish(projection_id)
    if not pid:
        return {"error": invalid_identifier_message("projection_id", projection_id)}

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            return {"error": f"Projection {projection_id} not found."}

        # Get the frame to find project_id
        frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
        if not frame:
            return {"error": "Associated frame not found."}

        feedback = Feedback(
            feedback_id=uuid.uuid4(),
            source_projection_id=pid,
            source_agent="projection_agent",
            target_frame_id=frame.frame_id,
            target_project_id=frame.project_id,
            category=issue,
            field_path=field,
            question=question,
            context=context,
            status=FeedbackStatus.OPEN,
        )
        session.add(feedback)

        # Mark projection as needing feedback if not already completed
        if projection.status != ProjectionStatus.COMPLETED:
            projection.status = ProjectionStatus.NEEDS_FEEDBACK

        session.commit()

        return {"feedback_id": str(feedback.feedback_id), "status": "created"}


PROJECTION_TOOLS = [
    get_frame_content,
    save_projection,
    flag_for_feedback,
]
