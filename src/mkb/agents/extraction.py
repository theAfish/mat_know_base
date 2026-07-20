"""
Knowledge-extraction agent built on google-adk.

Produces a structured "knowledge frame" for each research project.
Uses the AgentRunner for execution and flexible prompts for extraction.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from google.adk.agents import Agent

from mkb.agents._utils import JobCancelled, create_llm, run_async_sync
from mkb.agents.prompts.kb_extraction import EXTRACTION_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools import ALL_TOOLS
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import FrameStatus, KnowledgeFrame

logger = logging.getLogger(__name__)

APP_NAME = "mkb_extraction"


# =====================================================================
# Agent factory
# =====================================================================


def build_extraction_agent(model: str | None = None) -> Agent:
    """Create a configured extraction agent with all tools."""
    return Agent(
        name="knowledge_extractor",
        model=create_llm(model),
        instruction=EXTRACTION_PROMPT,
        tools=ALL_TOOLS,
    )


# =====================================================================
# Runner
# =====================================================================


async def _run_extraction_async(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    max_passes: int = 1,
    progress_callback=None,
) -> dict:
    """Core async extraction loop for one project."""

    def _emit(message: str, **extra) -> None:
        if progress_callback:
            progress_callback({"message": message, **extra})

    agent = build_extraction_agent(model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"extract_{project_id}"
    await runner.create_session(session_id)
    _emit("Preparing extraction run", stage="setup")

    # Track whether we actually set the frame to IN_PROGRESS so we can
    # revert it on cancellation.
    frame_was_in_progress = False

    # Mark frame as in-progress
    with SyncSessionLocal() as db:
        from mkb.db.models import ResearchProject
        project = db.query(ResearchProject).filter_by(project_id=project_id).first()
        if not project:
            return {"status": "error", "message": f"Project {project_id} not found"}
        project_label = project.label or str(project_id)

        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        if not frame:
            frame = KnowledgeFrame(
                frame_id=uuid.uuid4(),
                project_id=project_id,
                status=FrameStatus.IN_PROGRESS,
            )
            db.add(frame)
        else:
            frame.status = FrameStatus.IN_PROGRESS
        db.commit()
        frame_was_in_progress = True

    try:
        _emit(f"Frame marked in progress for {project_label}", stage="setup")

        # Pass 1: Initial extraction
        message = (
            f"Extract knowledge from project {project_id} "
            f"(label: {project_label}). "
            f"Follow the seed-then-incremental workflow: "
            f"list files, inspect headings and total length, seed the frame "
            f"once with save_knowledge_frame, then walk the paper section by "
            f"section and append items via update_knowledge_frame. Make sure "
            f"every section of every Markdown file is covered (page through "
            f"truncated reads until no [TRUNCATED] banner remains)."
        )

        result = await runner.run(
            session_id=session_id,
            message=message,
            verbose=verbose,
            progress_callback=progress_callback,
        )

        if not result.success:
            _emit("Extraction failed", stage="failed", status="FAILED")
            _mark_frame_failed(project_id, result.error)
            return {
                "status": "error",
                "project_id": str(project_id),
                "message": result.error,
            }

        # Save initial extraction pass
        _emit("Initial extraction pass completed", stage="initial_pass")
        _save_extraction_pass(project_id, pass_type="initial")

        # Passes 2..N: Review passes
        if max_passes > 1:
            from mkb.agents.review import run_review_pass

            for pass_num in range(2, max_passes + 1):
                logger.info("Running review pass %d/%d for project %s", pass_num, max_passes, project_id)
                _emit(f"Running review pass {pass_num}/{max_passes}", stage="review_pass")
                review_result = await run_review_pass(project_id, model=model, verbose=verbose)

                if review_result.get("no_changes"):
                    logger.info("Review pass %d: no significant changes needed, stopping early", pass_num)
                    _emit("Review pass found no further changes", stage="review_pass")
                    break

        # Check result
        with SyncSessionLocal() as db:
            frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
            frame_status = frame.status.value if frame else "unknown"
            content_keys = list((frame.content or {}).keys()) if frame else []

        return {
            "status": "completed" if frame_status == "COMPLETED" else frame_status,
            "project_id": str(project_id),
            "frame_status": frame_status,
            "content_sections": content_keys,
            "agent_summary": result.final_text,
            "total_events": len(result.events_collected),
        }

    except JobCancelled:
        # Revert the frame to PENDING so it can be re-extracted cleanly.
        if frame_was_in_progress:
            with SyncSessionLocal() as db:
                frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
                if frame and frame.status == FrameStatus.IN_PROGRESS:
                    frame.status = FrameStatus.PENDING
                    db.commit()
            logger.info("Extraction cancelled for project %s — frame reverted to PENDING", project_id)
        raise


def _mark_frame_failed(project_id: uuid.UUID, error: str | None):
    """Mark a frame as failed with error metadata."""
    with SyncSessionLocal() as db:
        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        if frame:
            frame.status = FrameStatus.FAILED
            meta = dict(frame.source_metadata or {})
            meta["error"] = error
            meta["failed_at"] = datetime.now(timezone.utc).isoformat()
            frame.source_metadata = meta
            db.commit()


def _save_extraction_pass(project_id: uuid.UUID, pass_type: str):
    """Save an ExtractionPass record for audit trail."""
    from mkb.db.models import ExtractionPass

    with SyncSessionLocal() as db:
        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        if frame:
            pass_record = ExtractionPass(
                pass_id=uuid.uuid4(),
                frame_id=frame.frame_id,
                # Frame versions are cumulative across re-extraction runs.  Using
                # a literal 1 here made every later initial pass look as though
                # it happened before the existing review history.
                pass_number=frame.extraction_version,
                pass_type=pass_type,
                content_snapshot=frame.content,
                agent_notes=frame.extraction_summary,
            )
            db.add(pass_record)
            db.commit()


def run_extraction(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    max_passes: int = 1,
    progress_callback=None,
) -> dict:
    """Synchronous wrapper — run extraction on one project."""
    return run_async_sync(
        _run_extraction_async(
            project_id,
            model,
            verbose,
            max_passes,
            progress_callback=progress_callback,
        )
    )


def run_extraction_all(
    limit: int | None = None,
    model: str | None = None,
    verbose: bool = False,
    max_passes: int = 1,
) -> dict:
    """Run extraction on all projects that don't have a completed frame."""
    with SyncSessionLocal() as db:
        from mkb.db.models import ResearchProject
        completed_project_ids = [
            f.project_id for f in
            db.query(KnowledgeFrame.project_id)
            .filter_by(status=FrameStatus.COMPLETED)
            .all()
        ]
        q = db.query(ResearchProject)
        if completed_project_ids:
            q = q.filter(~ResearchProject.project_id.in_(completed_project_ids))
        if limit:
            q = q.limit(limit)
        projects = q.all()
        project_ids = [p.project_id for p in projects]

    results = []
    for pid in project_ids:
        logger.info("Extracting project %s ...", pid)
        result = run_extraction(pid, model=model, verbose=verbose, max_passes=max_passes)
        results.append(result)
        logger.info("  → %s", result.get("status", "unknown"))

    return {
        "total_projects": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
