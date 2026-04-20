"""
Projection review tools for the projection reviewer agent.

These tools let the reviewer agent read all projections for a project,
access knowledge frames, save consolidated reviewed projections, and
delegate re-extraction to a fixer sub-agent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
    ReviewedProjection,
    Space,
)
from mkb.spaces.schema_utils import normalize_projection_data

logger = logging.getLogger(__name__)


def get_all_projections_for_review(space_id: str, project_id: str) -> dict:
    """Get all projection data for a space+project combination.

    Returns all projection runs (all timestamps) so the reviewer can
    compare, merge, and identify discrepancies.

    Args:
        space_id: The space to filter projections by.
        project_id: The project to filter projections by.

    Returns:
        Dict with space info, frame info, and list of all projections with data.
    """
    sid = parse_uuidish(space_id)
    if not sid:
        return {"error": invalid_identifier_message("space_id", space_id)}
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    with SyncSessionLocal() as session:
        space = session.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"error": f"Space {space_id} not found."}

        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return {"error": f"No knowledge frame found for project {project_id}."}

        projections = (
            session.query(Projection)
            .filter_by(space_id=sid, frame_id=frame.frame_id)
            .order_by(Projection.created_at.desc())
            .all()
        )

        if not projections:
            return {
                "error": f"No projections found for space {space_id} and project {project_id}.",
            }

        projection_list = []
        for proj in projections:
            normalized_data, validation = normalize_projection_data(
                proj.data or {},
                space.extraction_schema,
            )
            projection_list.append({
                "projection_id": str(proj.projection_id),
                "status": proj.status.value,
                "data": normalized_data,
                "validation_result": validation,
                "agent_notes": proj.agent_notes,
                "space_version": proj.space_version,
                "extracted_at": proj.extracted_at.isoformat() if proj.extracted_at else None,
                "created_at": proj.created_at.isoformat() if proj.created_at else None,
            })

        return {
            "space_id": str(sid),
            "space_name": space.name,
            "project_id": str(pid),
            "frame_id": str(frame.frame_id),
            "extraction_schema": space.extraction_schema,
            "total_projections": len(projection_list),
            "projections": projection_list,
        }


def get_frame_for_review(project_id: str) -> dict:
    """Get the knowledge frame content for cross-referencing during review.

    Args:
        project_id: The project whose frame to retrieve.

    Returns:
        Dict with frame content and metadata.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    with SyncSessionLocal() as session:
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return {"error": f"No knowledge frame found for project {project_id}."}
        return {
            "frame_id": str(frame.frame_id),
            "project_id": str(frame.project_id),
            "status": frame.status.value,
            "content": frame.content or {},
            "extraction_summary": frame.extraction_summary,
            "extraction_version": frame.extraction_version,
        }


def save_reviewed_projection(
    space_id: str,
    project_id: str,
    data: dict,
    review_notes: str = "",
    source_projection_ids: list | None = None,
) -> dict:
    """Save the consolidated reviewed projection.

    This creates or updates a single reviewed projection for a space+project,
    replacing any previous reviewed version.

    Args:
        space_id: The space this review applies to.
        project_id: The project this review applies to.
        data: The consolidated, corrected projection data.
        review_notes: Agent's review summary and corrections made.
        source_projection_ids: List of projection IDs that were reviewed/merged.

    Returns:
        Dict with reviewed_projection_id and status.
    """
    sid = parse_uuidish(space_id)
    if not sid:
        return {"error": invalid_identifier_message("space_id", space_id)}
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        space = session.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"error": f"Space {space_id} not found."}

        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return {"error": f"No knowledge frame found for project {project_id}."}

        # Normalize the data against the space schema
        normalized_data, validation_result = normalize_projection_data(
            data,
            space.extraction_schema,
        )

        # Inject source_project_id references
        from mkb.agents.tools.projection import _inject_source_project_references
        normalized_data = _inject_source_project_references(normalized_data, str(pid))

        # Upsert: replace existing reviewed projection for this space+project
        existing = (
            session.query(ReviewedProjection)
            .filter_by(space_id=sid, project_id=pid)
            .first()
        )

        if existing:
            existing.data = normalized_data
            existing.validation_result = validation_result or None
            existing.review_notes = review_notes
            existing.source_projection_ids = source_projection_ids or []
            existing.status = ProjectionStatus.REVIEWED
            existing.space_version = space.version
            existing.frame_id = frame.frame_id
            existing.reviewed_at = now
            session.commit()
            return {
                "reviewed_projection_id": str(existing.reviewed_projection_id),
                "status": "updated",
            }

        reviewed = ReviewedProjection(
            reviewed_projection_id=uuid.uuid4(),
            space_id=sid,
            project_id=pid,
            frame_id=frame.frame_id,
            data=normalized_data,
            validation_result=validation_result or None,
            review_notes=review_notes,
            source_projection_ids=source_projection_ids or [],
            status=ProjectionStatus.REVIEWED,
            space_version=space.version,
            reviewed_at=now,
        )
        session.add(reviewed)
        session.commit()

        return {
            "reviewed_projection_id": str(reviewed.reviewed_projection_id),
            "status": "created",
        }


def request_re_extraction(
    project_id: str,
    fields: str,
    context: str = "",
) -> dict:
    """Request the fixer sub-agent to re-examine specific fields against source data.

    Delegates to a projection fixer agent that reads the raw processed files
    and knowledge frame to verify and correct specific data points.

    Args:
        project_id: The project to re-examine.
        fields: Description of which fields to re-check and what seems wrong.
            Example: "catalysts[0].surface_area_m2_g is 500 but Table 2 shows 250"
        context: Additional context about what the reviewer found problematic.

    Returns:
        Dict with the fixer agent's corrections and findings.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    try:
        from mkb.agents.projection_fixer import run_fixer
        result = run_fixer(
            project_id=pid,
            fields=fields,
            context=context,
        )
        return result
    except Exception as exc:
        logger.error("Fixer sub-agent failed: %s", exc)
        return {
            "error": f"Re-extraction failed: {exc}",
            "fields": fields,
        }


PROJECTION_REVIEW_TOOLS = [
    get_all_projections_for_review,
    get_frame_for_review,
    save_reviewed_projection,
    request_re_extraction,
]
