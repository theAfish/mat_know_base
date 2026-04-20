"""
Projection reviewer agent — consolidates and corrects projection data
through multi-agent review.

Orchestrates the review process: reads all projections, compares against
knowledge frame and source material, delegates to fixer sub-agent when
needed, and produces a single reviewed projection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

from mkb.agents.prompts.projection_review import PROJECTION_REVIEW_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.reading import READING_TOOLS
from mkb.agents.tools.projection_review import PROJECTION_REVIEW_TOOLS
from mkb.config import settings
from mkb.db.engine import SyncSessionLocal, init_db
from mkb.db.models import (
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
    ReviewedProjection,
    Space,
)

logger = logging.getLogger(__name__)

APP_NAME = "mkb_projection_reviewer"

# Combine reading tools (for direct source verification) with review tools
REVIEWER_TOOLS = READING_TOOLS + PROJECTION_REVIEW_TOOLS


def build_projection_reviewer_agent(model: str | None = None) -> Agent:
    """Create a projection reviewer agent."""
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.openai_api_base:
        os.environ.setdefault("OPENAI_API_BASE", settings.openai_api_base)

    llm = LiteLlm(model=model or settings.extraction_model)
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

        # Check that projections exist
        projection_count = (
            db.query(Projection)
            .filter_by(space_id=space_id, frame_id=frame.frame_id)
            .filter(Projection.status == ProjectionStatus.COMPLETED)
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
        f"then systematically verify and consolidate the data into "
        f"a single reviewed projection."
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

    # Check result
    with SyncSessionLocal() as db:
        reviewed = (
            db.query(ReviewedProjection)
            .filter_by(space_id=space_id, project_id=project_id)
            .first()
        )
        reviewed_id = str(reviewed.reviewed_projection_id) if reviewed else None
        status = reviewed.status.value if reviewed else "unknown"

    return {
        "status": "completed" if status == "REVIEWED" else status,
        "reviewed_projection_id": reviewed_id,
        "space_id": str(space_id),
        "space_name": space_name,
        "project_id": str(project_id),
        "projections_reviewed": projection_count,
        "agent_summary": result.final_text,
    }


def run_projection_review(
    space_id: uuid.UUID,
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Synchronous wrapper — run projection review on one project."""
    init_db()
    return asyncio.run(_run_review_async(space_id, project_id, model, verbose))


def run_projection_review_all(
    space_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run projection review on all projects that have projections in a space."""
    init_db()
    sid = space_id

    with SyncSessionLocal() as db:
        space = db.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"status": "error", "message": f"Space {sid} not found"}

        # Find all projects with completed projections in this space
        projections = (
            db.query(Projection.frame_id)
            .filter_by(space_id=sid, status=ProjectionStatus.COMPLETED)
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
        result = run_projection_review(sid, pid, model=model, verbose=verbose)
        results.append(result)
        logger.info("  -> %s", result.get("status", "unknown"))

    return {
        "total_projects": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
