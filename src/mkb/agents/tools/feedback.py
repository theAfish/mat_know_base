"""
Feedback tools for agents.

These tools let agents create, query, and resolve feedback items
during knowledge extraction and projection.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Feedback, FeedbackStatus

logger = logging.getLogger(__name__)


def get_pending_feedback(project_id: str) -> list[dict]:
    """Get all open feedback items for a project.

    Used by the KB extraction agent during feedback review to see
    what issues have been flagged by projection agents.
    """
    pid = uuid.UUID(project_id)
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
    fid = uuid.UUID(feedback_id)
    with SyncSessionLocal() as session:
        fb = session.query(Feedback).filter_by(feedback_id=fid).first()
        if not fb:
            return {"error": f"Feedback {feedback_id} not found."}

        fb.status = FeedbackStatus(status)
        fb.resolution_notes = resolution_notes
        fb.resolved_by = "kb_agent"
        fb.resolved_at = datetime.now(timezone.utc)
        session.commit()

        return {"feedback_id": str(fb.feedback_id), "status": fb.status.value}


FEEDBACK_TOOLS = [
    get_pending_feedback,
    resolve_feedback_item,
]
