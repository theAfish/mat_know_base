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
from mkb.agents.prompts.projection_review import (
    PROJECTION_REVIEW_PROMPT,
    PROJECTION_REVIEW_QA_PROMPT,
    default_review_prompt_for,
)
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


def build_projection_reviewer_agent(
    model: str | None = None,
    purpose: str | None = None,
    custom_prompt: str | None = None,
) -> Agent:
    """Create a projection reviewer agent.

    Prompt selection order:
    1. ``custom_prompt`` — a per-space override from ``Space.review_prompt``.
    2. The default prompt for the space ``purpose`` (tabular / qa / skill /
       freeform), via :func:`default_review_prompt_for`.
    """
    llm = create_llm(model)
    purpose_key = (purpose or "tabular_database").lower()
    if custom_prompt and custom_prompt.strip():
        instruction = custom_prompt
        agent_name = f"projection_reviewer_{purpose_key}_custom"
    else:
        instruction = default_review_prompt_for(purpose_key)
        agent_name = (
            "projection_reviewer_qa"
            if purpose_key == "qa_benchmark"
            else f"projection_reviewer_{purpose_key}"
            if purpose_key in {"skill_cards", "freeform"}
            else "projection_reviewer"
        )
    return Agent(
        name=agent_name,
        model=llm,
        instruction=instruction,
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
            .filter(Projection.superseded_by_id.is_(None))
            .count()
        )
        if projection_count == 0:
            return {
                "status": "error",
                "message": f"No completed projections for space {space_id} and project {project_id}",
            }

        space_name = space.name
        space_purpose = getattr(space, "purpose", None)
        space_review_prompt = getattr(space, "review_prompt", None)

    agent = build_projection_reviewer_agent(
        model,
        purpose=space_purpose,
        custom_prompt=space_review_prompt,
    )
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
            .filter(Projection.superseded_by_id.is_(None))
            .order_by(Projection.reviewed_at.desc().nullslast())
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
    progress_callback=None,
    project_ids: list[uuid.UUID] | None = None,
) -> dict:
    """Run projection review on projects in a space (separate sessions).

    When ``project_ids`` is provided, only those projects are reviewed;
    otherwise every project with at least one live completed projection
    in the space is reviewed.
    """
    sid = space_id

    with SyncSessionLocal() as db:
        space = db.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"status": "error", "message": f"Space {sid} not found"}

        if project_ids:
            # Resolve frame_ids for the requested projects in this space
            requested = list(project_ids)
            frames = (
                db.query(KnowledgeFrame)
                .filter(KnowledgeFrame.project_id.in_(requested))
                .all()
            )
            frame_by_pid = {f.project_id: f.frame_id for f in frames}
            # Keep only those that have a non-deleted completed projection
            live_frames = (
                db.query(Projection.frame_id)
                .filter_by(space_id=sid, status=ProjectionStatus.COMPLETED)
                .filter(Projection.deleted_at.is_(None))
                .filter(Projection.superseded_by_id.is_(None))
                .filter(Projection.frame_id.in_(list(frame_by_pid.values())))
                .distinct()
                .all()
            )
            live_frame_ids = {row[0] for row in live_frames}
            project_id_list = [
                pid for pid in requested if frame_by_pid.get(pid) in live_frame_ids
            ]
            if not project_id_list:
                return {
                    "status": "error",
                    "message": "None of the requested projects have completed projections in this space",
                }
        else:
            projections = (
                db.query(Projection.frame_id)
                .filter_by(space_id=sid, status=ProjectionStatus.COMPLETED)
                .filter(Projection.deleted_at.is_(None))
                .filter(Projection.superseded_by_id.is_(None))
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
            project_id_list = [f.project_id for f in frames]

    results = []
    total = len(project_id_list)
    for idx, pid in enumerate(project_id_list, start=1):
        logger.info("Reviewing projections for project %s in space %s ...", pid, sid)
        if progress_callback:
            progress_callback({
                "message": f"Reviewing project {idx}/{total} ({str(pid)[:8]})",
            })
        result = await _run_review_async(sid, pid, model=model, verbose=verbose)
        results.append(result)
        logger.info("  -> %s", result.get("status", "unknown"))

    return {
        "total_projects": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }


@sync_agent_run
async def run_projection_review_session(
    space_id: uuid.UUID,
    project_ids: list[uuid.UUID],
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
) -> dict:
    """Run a SINGLE reviewer session that handles every selected project.

    The same agent context (and conversation/session) reviews each project
    in turn. The agent is instructed to call ``save_reviewed_projection``
    after each project before moving on to the next. This is useful when
    cross-project context (e.g. shared schemas, recurring patterns) helps
    the reviewer produce more consistent decisions.
    """
    sid = space_id

    with SyncSessionLocal() as db:
        space = db.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"status": "error", "message": f"Space {sid} not found"}

        # Filter to projects that actually have live completed projections
        frames = (
            db.query(KnowledgeFrame)
            .filter(KnowledgeFrame.project_id.in_(list(project_ids)))
            .all()
        )
        frame_by_pid = {f.project_id: f.frame_id for f in frames}
        if not frame_by_pid:
            return {"status": "error", "message": "No knowledge frames found for selected projects"}

        per_project_counts: dict[uuid.UUID, int] = {}
        for pid in project_ids:
            fid = frame_by_pid.get(pid)
            if not fid:
                continue
            n = (
                db.query(Projection)
                .filter_by(space_id=sid, frame_id=fid, status=ProjectionStatus.COMPLETED)
                .filter(Projection.deleted_at.is_(None))
                .filter(Projection.superseded_by_id.is_(None))
                .count()
            )
            if n > 0:
                per_project_counts[pid] = n

        if not per_project_counts:
            return {
                "status": "error",
                "message": "None of the selected projects have completed projections in this space",
            }

        space_name = space.name
        space_purpose = getattr(space, "purpose", None)
        space_review_prompt = getattr(space, "review_prompt", None)

    agent = build_projection_reviewer_agent(
        model,
        purpose=space_purpose,
        custom_prompt=space_review_prompt,
    )
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"review_sess_{sid}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    project_lines = "\n".join(
        f"  {i+1}. project_id={pid} ({n} completed projection run(s))"
        for i, (pid, n) in enumerate(per_project_counts.items())
    )
    message = (
        f"You are running a SINGLE consolidated review session over "
        f"{len(per_project_counts)} project(s) in space {sid} ('{space_name}').\n\n"
        f"Projects to review (one at a time, in order):\n{project_lines}\n\n"
        f"For EACH project, in order:\n"
        f"  1. Call get_all_projections_for_review(space_id, project_id) to "
        f"load that project's projection runs.\n"
        f"  2. Call get_frame_for_review(project_id) to load its knowledge frame.\n"
        f"  3. Apply your usual review process and call "
        f"save_reviewed_projection(winning_projection_id, data, review_notes) "
        f"for that project BEFORE moving on to the next.\n"
        f"  4. Carry over any patterns / standards you established from earlier "
        f"projects to keep decisions consistent across the batch.\n\n"
        f"Do not skip any project. Report a brief per-project summary at the end."
    )

    if progress_callback:
        progress_callback({
            "message": f"Reviewing {len(per_project_counts)} project(s) in one session",
        })

    result = await runner.run(
        session_id=session_id,
        message=message,
        verbose=verbose,
    )

    if not result.success:
        return {
            "status": "error",
            "space_id": str(sid),
            "project_ids": [str(p) for p in per_project_counts.keys()],
            "message": result.error,
        }

    # Tally reviewed projections per project after the run
    summary: list[dict] = []
    with SyncSessionLocal() as db:
        for pid in per_project_counts.keys():
            fid = frame_by_pid.get(pid)
            reviewed = (
                db.query(Projection)
                .filter_by(space_id=sid, frame_id=fid, status=ProjectionStatus.REVIEWED)
                .filter(Projection.deleted_at.is_(None))
                .filter(Projection.superseded_by_id.is_(None))
                .order_by(Projection.reviewed_at.desc().nullslast())
                .first()
            )
            summary.append({
                "project_id": str(pid),
                "status": "completed" if reviewed else "unknown",
                "projection_id": str(reviewed.projection_id) if reviewed else None,
            })

    return {
        "mode": "session",
        "space_id": str(sid),
        "space_name": space_name,
        "total_projects": len(per_project_counts),
        "completed": sum(1 for s in summary if s["status"] == "completed"),
        "results": summary,
        "agent_summary": result.final_text,
    }
