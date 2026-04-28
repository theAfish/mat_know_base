"""
Feedback tools for agents.

These tools let agents create, query, and resolve feedback items
during knowledge extraction and projection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Feedback, FeedbackStatus, KnowledgeFrame

logger = logging.getLogger(__name__)


def get_pending_feedback(project_id: str) -> list[dict]:
    """Get all open feedback items for a project.

    Used by the KB extraction agent during feedback review to see
    what issues have been flagged by projection agents.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return [{"error": invalid_identifier_message("project_id", project_id)}]

    with SyncSessionLocal() as session:
        items = (
            session.query(Feedback)
            .filter_by(target_project_id=pid, status=FeedbackStatus.OPEN)
            .order_by(Feedback.created_at)
            .all()
        )
        return [
            {
                "feedback_id": str(fb.feedback_id),
                "category": fb.category,
                "field_path": fb.field_path,
                "question": fb.question,
                "context": fb.context,
                "source_agent": fb.source_agent,
            }
            for fb in items
        ]


def resolve_feedback_item(
    feedback_id: str,
    status: str,
    resolution_notes: str = "",
) -> dict:
    """Resolve a feedback item.

    Args:
        feedback_id: The feedback to resolve.
        status: New status — one of: RESOLVED, DISMISSED, DEV_ISSUE.
        resolution_notes: Explanation of the resolution.

    Returns:
        Dict with feedback_id and new status.
    """
    fid = parse_uuidish(feedback_id)
    if not fid:
        return {"error": invalid_identifier_message("feedback_id", feedback_id)}

    try:
        resolved_status = FeedbackStatus(status)
    except ValueError:
        return {"error": f"Invalid status: {status!r}"}

    with SyncSessionLocal() as session:
        fb = session.query(Feedback).filter_by(feedback_id=fid).first()
        if not fb:
            return {"error": f"Feedback {feedback_id} not found."}

        fb.status = resolved_status
        fb.resolution_notes = resolution_notes
        fb.resolved_by = "kb_agent"
        fb.resolved_at = datetime.now(timezone.utc)
        session.commit()

        # Write the resolved item into the frame's agent_annotations so future
        # runs know this feedback was already handled and skip re-flagging it.
        resolved_annotation = {
            "feedback_id": str(fb.feedback_id),
            "category": fb.category,
            "field_path": fb.field_path,
            "question": fb.question,
            "resolution_notes": resolution_notes,
            "status": resolved_status.value,
            "resolved_at": fb.resolved_at.isoformat(),
        }
        frame = session.query(KnowledgeFrame).filter_by(frame_id=fb.target_frame_id).first()
        if frame:
            annotations = dict(frame.agent_annotations or {})
            resolved_list = list(annotations.get("resolved_feedback", []))
            resolved_list.append(resolved_annotation)
            annotations["resolved_feedback"] = resolved_list
            frame.agent_annotations = annotations
            session.commit()

        return {"feedback_id": str(fb.feedback_id), "status": fb.status.value}


FEEDBACK_TOOLS = [
    get_pending_feedback,
    resolve_feedback_item,
]
