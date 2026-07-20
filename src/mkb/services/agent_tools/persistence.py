from __future__ import annotations

from datetime import datetime, timezone

from mkb.db.models import ProjectionStatus


def complete_projection(session, projection, *, data: dict, validation: dict | None, agent_notes: str) -> None:
    projection.data = data
    projection.validation_result = validation or None
    projection.agent_notes = agent_notes
    projection.status = ProjectionStatus.COMPLETED
    projection.extracted_at = datetime.now(timezone.utc)
    session.commit()


def fail_projection(session, projection, *, message: str, validation: dict | None = None) -> None:
    projection.status = ProjectionStatus.FAILED
    projection.agent_notes = message
    projection.validation_result = validation or None
    session.commit()
