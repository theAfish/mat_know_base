"""
Projection reviewer agent — consolidates and corrects projection data
through multi-agent review.

Orchestrates the review process: reads all projections, compares against
knowledge frame and source material, delegates to fixer sub-agent when
needed, picks the best projection, updates it in-place, and soft-deletes
the rest.
"""

from __future__ import annotations

import logging
import uuid

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, sync_agent_run
from mkb.agents.prompts.projection_review import PROJECTION_REVIEW_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.reading import READING_TOOLS
from mkb.agents.tools.projection_review import PROJECTION_REVIEW_TOOLS
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
    Space,
)

logger = logging.getLogger(__name__)

APP_NAME = "mkb_projection_reviewer"

# Combine reading tools (for direct source verification) with review tools
REVIEWER_TOOLS = READING_TOOLS + PROJECTION_REVIEW_TOOLS


def build_projection_reviewer_agent(model: str | None = None) -> Agent:
    """Create a projection reviewer agent."""
    llm = create_llm(model)
    return Agent(
        name="projection_reviewer",
        model=llm,
        instruction=PROJECTION_REVIEW_PROMPT,
        tools=REVIEWER_TOOLS,
    )


async def _run_review_async(
    space_id: uuid.UUID,
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run projection review on a single project for a given space."""

    with SyncSessionLocal() as db:
        space = db.query(Space).filter_by(space_id=space_id).first()
        if not space:
            return {"status": "error", "message": f"Space {space_id} not found"}

        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        if not frame:
            return {"status": "error", "message": f"No frame found for project {project_id}"}

        # Count non-deleted completed projections
        projection_count = (
            db.query(Projection)
            .filter_by(space_id=space_id, frame_id=frame.frame_id)
            .filter(Projection.status == ProjectionStatus.COMPLETED)
            .filter(Projection.deleted_at.is_(None))
            .count()
        )
        if projection_count == 0:
            return {
                "status": "error",
                "message": f"No completed projections for space {space_id} and project {project_id}",
            }

        space_name = space.name

    agent = build_projection_reviewer_agent(model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"review_proj_{space_id}_{project_id}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    message = (
        f"Review all projections for space {space_id} ('{space_name}') "
        f"and project {project_id}. "
        f"There are {projection_count} completed projection run(s) to review. "
        f"Start by loading all projections and the knowledge frame, "
        f"then systematically verify the data, pick the best projection "
        f"as the winner, merge corrections, and save it."
    )

    result = await runner.run(
        session_id=session_id,
        message=message,
        verbose=verbose,
    )

    if not result.success:
        return {
            "status": "error",
            "space_id": str(space_id),
            "project_id": str(project_id),
            "message": result.error,
        }

    # Check result — look for a REVIEWED projection
    with SyncSessionLocal() as db:
        reviewed = (
            db.query(Projection)
            .filter_by(space_id=space_id, status=ProjectionStatus.REVIEWED)
            .join(KnowledgeFrame, Projection.frame_id == KnowledgeFrame.frame_id)
            .filter(KnowledgeFrame.project_id == project_id)
            .filter(Projection.deleted_at.is_(None))
            .first()
        )
        projection_id = str(reviewed.projection_id) if reviewed else None
        status = reviewed.status.value if reviewed else "unknown"

    return {
        "status": "completed" if status == "REVIEWED" else status,
        "projection_id": projection_id,
        "space_id": str(space_id),
        "space_name": space_name,
        "project_id": str(project_id),
        "projections_reviewed": projection_count,
        "agent_summary": result.final_text,
    }


@sync_agent_run
async def run_projection_review(
    space_id: uuid.UUID,
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run projection review on one project."""
    return await _run_review_async(space_id, project_id, model, verbose)


@sync_agent_run
async def run_projection_review_all(
    space_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run projection review on all projects that have projections in a space."""
    sid = space_id

    with SyncSessionLocal() as db:
        space = db.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"status": "error", "message": f"Space {sid} not found"}

        # Find all projects with non-deleted completed projections in this space
        projections = (
            db.query(Projection.frame_id)
            .filter_by(space_id=sid, status=ProjectionStatus.COMPLETED)
            .filter(Projection.deleted_at.is_(None))
            .distinct()
            .all()
        )
        frame_ids = [p[0] for p in projections]

        if not frame_ids:
            return {"status": "error", "message": "No completed projections in this space"}

        frames = (
            db.query(KnowledgeFrame)
            .filter(KnowledgeFrame.frame_id.in_(frame_ids))
            .all()
        )
        project_ids = [f.project_id for f in frames]

    results = []
    for pid in project_ids:
        logger.info("Reviewing projections for project %s in space %s ...", pid, sid)
        result = await _run_review_async(sid, pid, model=model, verbose=verbose)
        results.append(result)
        logger.info("  -> %s", result.get("status", "unknown"))

    return {
        "total_projects": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
