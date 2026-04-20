"""
Projection review tools for the projection reviewer agent.

These tools let the reviewer agent read all projections for a project,
access knowledge frames, save the winning projection (updating it in-place
and soft-deleting the rest), and delegate re-extraction to a fixer sub-agent.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
    Space,
)
from mkb.spaces.schema_utils import normalize_projection_data

logger = logging.getLogger(__name__)


def get_all_projections_for_review(space_id: str, project_id: str) -> dict:
    """Get all projection data for a space+project combination.

    Returns all non-deleted projection runs (grouped by timestamp) so the
    reviewer can compare, pick the best, and merge corrections.

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
            .filter(Projection.deleted_at.is_(None))
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
                "times_reviewed": proj.times_reviewed,
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
    winning_projection_id: str,
    data: dict,
    review_notes: str = "",
) -> dict:
    """Save the reviewed projection by updating the winning projection in-place.

    Updates the winning projection's data with the corrected/consolidated
    version, increments its review count, and soft-deletes all other
    projections for the same space+frame.

    Args:
        winning_projection_id: The projection ID chosen as the winner.
        data: The corrected, consolidated projection data.
        review_notes: Reviewer's summary of corrections and decisions made.

    Returns:
        Dict with projection_id, status, and count of soft-deleted projections.
    """
    wid = parse_uuidish(winning_projection_id)
    if not wid:
        return {"error": invalid_identifier_message("winning_projection_id", winning_projection_id)}

    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        winner = session.query(Projection).filter_by(projection_id=wid).first()
        if not winner:
            return {"error": f"Projection {winning_projection_id} not found."}

        space = session.query(Space).filter_by(space_id=winner.space_id).first()
        if not space:
            return {"error": f"Space {winner.space_id} not found."}

        # Normalize the data against the space schema
        normalized_data, validation_result = normalize_projection_data(
            data,
            space.extraction_schema,
        )

        # Inject source_project_id references
        frame = session.query(KnowledgeFrame).filter_by(frame_id=winner.frame_id).first()
        if frame:
            from mkb.agents.tools.projection import _inject_source_project_references
            normalized_data = _inject_source_project_references(
                normalized_data, str(frame.project_id)
            )

        # Update the winner in-place
        winner.data = normalized_data
        winner.validation_result = validation_result or None
        winner.review_notes = review_notes
        winner.status = ProjectionStatus.REVIEWED
        winner.times_reviewed = winner.times_reviewed + 1
        winner.reviewed_at = now

        # Soft-delete all OTHER projections for the same space+frame
        others = (
            session.query(Projection)
            .filter_by(space_id=winner.space_id, frame_id=winner.frame_id)
            .filter(Projection.projection_id != wid)
            .filter(Projection.deleted_at.is_(None))
            .all()
        )
        for other in others:
            other.deleted_at = now

        session.commit()

        return {
            "projection_id": str(winner.projection_id),
            "status": "reviewed",
            "times_reviewed": winner.times_reviewed,
            "soft_deleted_count": len(others),
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
