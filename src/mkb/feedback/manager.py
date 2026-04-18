"""
Feedback manager — creates, queries, and resolves feedback items.

Feedback flows from projection agents (which find unclear data)
back to KB extraction agents (which can fix the source frame).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Feedback, FeedbackStatus

logger = logging.getLogger(__name__)


def create_feedback(
    target_frame_id: uuid.UUID,
    target_project_id: uuid.UUID,
    category: str,
    question: str,
    source_agent: str = "user",
    source_projection_id: uuid.UUID | None = None,
    field_path: str | None = None,
    context: str | None = None,
) -> dict:
    """Create a new feedback item."""
    with SyncSessionLocal() as session:
        fb = Feedback(
            feedback_id=uuid.uuid4(),
            source_projection_id=source_projection_id,
            source_agent=source_agent,
            target_frame_id=target_frame_id,
            target_project_id=target_project_id,
            category=category,
            field_path=field_path,
            question=question,
            context=context,
            status=FeedbackStatus.OPEN,
        )
        session.add(fb)
        session.commit()
        return {"feedback_id": str(fb.feedback_id), "status": "created"}


def get_open_feedback(project_id: uuid.UUID) -> list[dict]:
    """Get all open feedback items for a project."""
    pid = project_id
    with SyncSessionLocal() as session:
        items = (
            session.query(Feedback)
            .filter_by(target_project_id=pid, status=FeedbackStatus.OPEN)
            .order_by(Feedback.created_at)
            .all()
        )
        return [_feedback_to_dict(fb) for fb in items]


def get_feedback_summary(project_id: uuid.UUID) -> dict:
    """Get counts of feedback by category and status for a project."""
    pid = project_id
    with SyncSessionLocal() as session:
        items = session.query(Feedback).filter_by(target_project_id=pid).all()
        by_status = {}
        by_category = {}
        for fb in items:
            by_status[fb.status.value] = by_status.get(fb.status.value, 0) + 1
            by_category[fb.category] = by_category.get(fb.category, 0) + 1
        return {
            "total": len(items),
            "by_status": by_status,
            "by_category": by_category,
        }


def resolve_feedback(
    feedback_id: uuid.UUID,
    status: str,
    notes: str,
    resolved_by: str = "user",
) -> dict:
    """Resolve a feedback item."""
    fid = feedback_id
    with SyncSessionLocal() as session:
        fb = session.query(Feedback).filter_by(feedback_id=fid).first()
        if not fb:
            return {"error": f"Feedback {feedback_id} not found."}

        fb.status = FeedbackStatus(status)
        fb.resolution_notes = notes
        fb.resolved_by = resolved_by
        fb.resolved_at = datetime.now(timezone.utc)
        session.commit()

        return {"feedback_id": str(fb.feedback_id), "status": fb.status.value}


def list_feedback(
    project_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[dict]:
    """List feedback items with optional filters."""
    with SyncSessionLocal() as session:
        q = session.query(Feedback).order_by(Feedback.created_at.desc())
        if project_id:
            q = q.filter_by(target_project_id=project_id)
        if status:
            q = q.filter_by(status=FeedbackStatus(status))
        return [_feedback_to_dict(fb) for fb in q.all()]


def _feedback_to_dict(fb: Feedback) -> dict:
    return {
        "feedback_id": str(fb.feedback_id),
        "source_agent": fb.source_agent,
        "source_projection_id": str(fb.source_projection_id) if fb.source_projection_id else None,
        "target_frame_id": str(fb.target_frame_id),
        "target_project_id": str(fb.target_project_id),
        "category": fb.category,
        "field_path": fb.field_path,
        "question": fb.question,
        "context": fb.context,
        "status": fb.status.value,
        "resolution_notes": fb.resolution_notes,
        "resolved_by": fb.resolved_by,
        "resolved_at": fb.resolved_at.isoformat() if fb.resolved_at else None,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
    }
