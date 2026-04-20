"""
Projection tools for the projection agent.

These tools let the projection agent read knowledge frame content,
save projection results, and flag data for feedback.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    Feedback,
    FeedbackStatus,
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
)

logger = logging.getLogger(__name__)


def _inject_source_project_references(data, source_project_id: str):
    """Recursively attach source-project references to extracted records."""
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
        if "references" in lower_keys:
            for key in list(enriched.keys()):
                if str(key).lower() == "references":
                    enriched[key] = source_project_id
        elif "reference" in lower_keys:
            for key in list(enriched.keys()):
                if str(key).lower() == "reference":
                    enriched[key] = source_project_id
        else:
            scalar_fields = [
                key for key, value in enriched.items()
                if not isinstance(value, (dict, list))
            ]
            if scalar_fields:
                enriched["references"] = source_project_id

        return enriched

    return data


def get_frame_content(frame_id: str) -> dict:
    """Read the knowledge frame content for projection.

    Returns the full frame content dict, or an error if not found.
    """
    fid = uuid.UUID(frame_id)
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
    pid = uuid.UUID(projection_id)
    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            return {"error": f"Projection {projection_id} not found."}

        frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
        source_project_id = str(frame.project_id) if frame else None
        if source_project_id:
            data = _inject_source_project_references(data, source_project_id)

        projection.data = data
        projection.validation_result = {"notes": validation_notes} if validation_notes else None
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
    pid = uuid.UUID(projection_id)

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
