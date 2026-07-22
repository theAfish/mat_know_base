"""Frames API service functions."""

from __future__ import annotations

from mkb.services._api_common import uuid
from mkb.agents.runtime import AgentRuntime
from mkb.ports import Database, ObjectStore


def extract(
    project_id: str | uuid.UUID | None = None,
    model: str | None = None,
    verbose: bool = False,
    max_passes: int = 1,
    progress_callback=None,
    *,
    database: Database,
    object_store: ObjectStore | None = None,
) -> dict:
    """Run knowledge extraction. If project_id given, extract one project.
    Otherwise extract all pending projects.

    Args:
        project_id: Optional specific project to extract.
        model: LLM model override.
        verbose: Enable verbose logging.
        max_passes: Number of extraction passes (1=initial only, >1 includes review).
    """
    from mkb.agents.extraction import run_extraction, run_extraction_all

    if project_id is not None:
        pid = uuid.UUID(str(project_id))
        return run_extraction(
            pid,
            model=model,
            verbose=verbose,
            max_passes=max_passes,
            progress_callback=progress_callback,
            runtime=AgentRuntime(database, object_store),
        )
    return run_extraction_all(
        model=model, verbose=verbose, max_passes=max_passes,
        runtime=AgentRuntime(database, object_store),
    )


# ── Knowledge Frames ─────────────────────────────────────────────

def get_frame(project_id: str | uuid.UUID, *, database: Database) -> dict | None:
    """Get the knowledge frame for a project. Returns None if not found."""
    from mkb.db.models import KnowledgeFrame

    pid = uuid.UUID(str(project_id))
    with database.session() as session:
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return None
        return {
            "frame_id": str(frame.frame_id),
            "project_id": str(frame.project_id),
            "status": frame.status.value,
            "content": frame.content,
            "extraction_summary": frame.extraction_summary,
            "times_checked": frame.times_checked,
            "extraction_version": frame.extraction_version,
            "extracted_at": frame.extracted_at.isoformat() if frame.extracted_at else None,
            "source_metadata": frame.source_metadata,
            "agent_annotations": frame.agent_annotations or {},
            "created_at": frame.created_at.isoformat() if frame.created_at else None,
            "updated_at": frame.updated_at.isoformat() if frame.updated_at else None,
        }

def list_frames(status: str | None = None, *, database: Database) -> list[dict]:
    """List all knowledge frames, optionally filtered by status."""
    from mkb.db.models import FrameStatus, KnowledgeFrame

    with database.session() as session:
        q = session.query(KnowledgeFrame).order_by(KnowledgeFrame.created_at.desc())
        if status:
            q = q.filter_by(status=FrameStatus(status))
        frames = q.all()
        return [
            {
                "frame_id": str(f.frame_id),
                "project_id": str(f.project_id),
                "status": f.status.value,
                "times_checked": f.times_checked,
                "extraction_version": f.extraction_version,
                "extracted_at": f.extracted_at.isoformat() if f.extracted_at else None,
                "extraction_summary": f.extraction_summary,
            }
            for f in frames
        ]

def get_extraction_history(
    project_id: str | uuid.UUID,
    *,
    database: Database,
) -> list[dict]:
    """Get the extraction pass history for a project's frame."""
    from mkb.db.models import ExtractionPass, KnowledgeFrame

    pid = uuid.UUID(str(project_id))
    with database.session() as session:
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return []
        passes = (
            session.query(ExtractionPass)
            .filter_by(frame_id=frame.frame_id)
            .order_by(ExtractionPass.created_at.desc(), ExtractionPass.pass_number.desc())
            .all()
        )
        return [
            {
                "pass_id": str(p.pass_id),
                "pass_number": p.pass_number,
                "pass_type": p.pass_type,
                "changes_made": p.changes_made,
                "agent_notes": p.agent_notes,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in passes
        ]


# ── Projects & Assets ────────────────────────────────────────────
