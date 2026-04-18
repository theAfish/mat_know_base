"""
Primary Python API for the Materials Knowledge Base.

All public functions return plain dicts. This module is the recommended
interface; the CLI is a thin wrapper around these functions.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from mkb.db.engine import SyncSessionLocal, init_db

logger = logging.getLogger(__name__)


# ── Lifecycle ────────────────────────────────────────────────────


def setup() -> None:
    """Ensure database tables exist (idempotent)."""
    init_db()


def reset_db() -> None:
    """Drop all tables and recreate them. Destructive!"""
    from mkb.db.engine import sync_engine
    from mkb.db.models import Base

    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    logger.info("Database reset complete.")


# ── Ingestion / Sync ─────────────────────────────────────────────


def ingest(directory: str | Path, label: str | None = None) -> dict:
    """Ingest a single project directory.

    Creates or updates a ResearchProject record keyed on the directory path,
    then ingests any new files found inside it.

    Returns a summary dict with counts (total, ingested, duplicates, errors).
    """
    from mkb.ingest.worker import ingest_directory

    return ingest_directory(directory, label=label)


def sync(root_dir: str | Path) -> dict:
    """Sync all project subfolders under *root_dir*.

    Each immediate subdirectory of *root_dir* is treated as one research
    project.  New subfolders are registered as new projects; existing projects
    are scanned for new files.

    Returns a summary dict with per-project results.
    """
    from mkb.ingest.worker import sync_root

    return sync_root(root_dir)


def sync_project(project_id: str | uuid.UUID) -> dict:
    """Re-scan a single project's source directory for new files.

    Returns a summary dict with counts of newly ingested files.
    """
    from mkb.ingest.worker import sync_project as _sync_project

    pid = uuid.UUID(str(project_id))
    return _sync_project(pid)


# ── Processing ───────────────────────────────────────────────────


def process(project_id: str | uuid.UUID | None = None) -> dict:
    """Process assets. If project_id is given, process only that project's assets.
    Otherwise process all pending assets.

    Returns a summary dict.
    """
    from mkb.processors.coordinator import process_all_pending, process_asset

    if project_id is not None:
        pid = uuid.UUID(str(project_id))
        from mkb.db.models import ProjectAsset
        with SyncSessionLocal() as session:
            links = session.query(ProjectAsset).filter_by(project_id=pid).all()
            asset_ids = [l.asset_id for l in links]

        results = []
        for aid in asset_ids:
            try:
                r = process_asset(aid)
                results.append(r)
            except Exception as exc:
                results.append({"asset_id": str(aid), "error": str(exc)})
        return {"project_id": str(pid), "assets_processed": len(results), "results": results}

    return process_all_pending()


# ── Extraction ───────────────────────────────────────────────────


def extract(
    project_id: str | uuid.UUID | None = None,
    model: str | None = None,
    verbose: bool = False,
    max_passes: int = 1,
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
        return run_extraction(pid, model=model, verbose=verbose, max_passes=max_passes)
    return run_extraction_all(model=model, verbose=verbose, max_passes=max_passes)


# ── Knowledge Frames ─────────────────────────────────────────────


def get_frame(project_id: str | uuid.UUID) -> dict | None:
    """Get the knowledge frame for a project. Returns None if not found."""
    from mkb.db.models import KnowledgeFrame

    pid = uuid.UUID(str(project_id))
    with SyncSessionLocal() as session:
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
            "created_at": frame.created_at.isoformat() if frame.created_at else None,
            "updated_at": frame.updated_at.isoformat() if frame.updated_at else None,
        }


def list_frames(status: str | None = None) -> list[dict]:
    """List all knowledge frames, optionally filtered by status."""
    from mkb.db.models import FrameStatus, KnowledgeFrame

    with SyncSessionLocal() as session:
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


def get_extraction_history(project_id: str | uuid.UUID) -> list[dict]:
    """Get the extraction pass history for a project's frame."""
    from mkb.db.models import ExtractionPass, KnowledgeFrame

    pid = uuid.UUID(str(project_id))
    with SyncSessionLocal() as session:
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return []
        passes = (
            session.query(ExtractionPass)
            .filter_by(frame_id=frame.frame_id)
            .order_by(ExtractionPass.pass_number)
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


def list_projects(limit: int = 50) -> list[dict]:
    """List research projects."""
    from mkb.db.models import KnowledgeFrame, ProjectAsset, ResearchProject

    with SyncSessionLocal() as session:
        projects = (
            session.query(ResearchProject)
            .order_by(ResearchProject.created_at.desc())
            .limit(limit)
            .all()
        )
        result = []
        for p in projects:
            asset_count = session.query(ProjectAsset).filter_by(project_id=p.project_id).count()
            frame = session.query(KnowledgeFrame).filter_by(project_id=p.project_id).first()
            result.append({
                "project_id": str(p.project_id),
                "label": p.label,
                "source_path": p.source_path,
                "file_count": p.file_count,
                "asset_count": asset_count,
                "frame_status": frame.status.value if frame else "NO_FRAME",
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })
        return result


def list_assets(project_id: str | uuid.UUID | None = None, limit: int = 100) -> list[dict]:
    """List assets, optionally filtered by project."""
    from mkb.db.models import Asset, ProjectAsset

    with SyncSessionLocal() as session:
        if project_id is not None:
            pid = uuid.UUID(str(project_id))
            links = session.query(ProjectAsset).filter_by(project_id=pid).all()
            asset_ids = [l.asset_id for l in links]
            if not asset_ids:
                return []
            assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all()
        else:
            assets = (
                session.query(Asset)
                .order_by(Asset.created_at.desc())
                .limit(limit)
                .all()
            )
        return [
            {
                "asset_id": str(a.asset_id),
                "filename": a.filename,
                "mime_type": a.mime_type,
                "size_bytes": a.size_bytes,
                "status": a.status.value,
            }
            for a in assets
        ]


# ── Spaces ───────────────────────────────────────────────────────


def create_space(
    name: str,
    domain: str,
    extraction_schema: dict,
    system_prompt: str,
    field_descriptions: dict,
    description: str | None = None,
) -> dict:
    """Create a new space (domain-specific extraction configuration)."""
    from mkb.spaces.registry import create_space as _create

    return _create(
        name=name,
        domain=domain,
        extraction_schema=extraction_schema,
        system_prompt=system_prompt,
        field_descriptions=field_descriptions,
        description=description,
    )


def list_spaces() -> list[dict]:
    """List all spaces."""
    from mkb.spaces.registry import list_spaces as _list

    return _list()


def get_space(space_id_or_name: str) -> dict | None:
    """Get a space by ID or name."""
    from mkb.spaces.registry import get_space as _get

    return _get(space_id_or_name)


# ── Projections ──────────────────────────────────────────────────


def project(
    space_id: str | uuid.UUID,
    frame_id: str | uuid.UUID | None = None,
    project_id: str | uuid.UUID | None = None,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run projection on one or more frames using a space definition.

    If frame_id given, project that specific frame.
    If project_id given, find the frame for that project and project it.
    """
    from mkb.agents.projection import run_projection
    from mkb.db.models import KnowledgeFrame

    sid = uuid.UUID(str(space_id))

    if frame_id:
        fid = uuid.UUID(str(frame_id))
        return run_projection(sid, fid, model=model, verbose=verbose)

    if project_id:
        pid = uuid.UUID(str(project_id))
        with SyncSessionLocal() as session:
            frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
            if not frame:
                return {"error": f"No frame for project {project_id}"}
            fid = frame.frame_id
        return run_projection(sid, fid, model=model, verbose=verbose)

    return {"error": "Must specify frame_id or project_id"}


def project_all(
    space_id: str | uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run projection on all completed frames using a space definition."""
    from mkb.agents.projection import run_projection_all

    sid = uuid.UUID(str(space_id))
    return run_projection_all(sid, model=model, verbose=verbose)


def get_projection(projection_id: str | uuid.UUID) -> dict | None:
    """Get a projection by ID."""
    from mkb.db.models import Projection

    pid = uuid.UUID(str(projection_id))
    with SyncSessionLocal() as session:
        proj = session.query(Projection).filter_by(projection_id=pid).first()
        if not proj:
            return None
        return {
            "projection_id": str(proj.projection_id),
            "space_id": str(proj.space_id),
            "frame_id": str(proj.frame_id),
            "status": proj.status.value,
            "data": proj.data,
            "validation_result": proj.validation_result,
            "agent_notes": proj.agent_notes,
            "extracted_at": proj.extracted_at.isoformat() if proj.extracted_at else None,
            "space_version": proj.space_version,
            "created_at": proj.created_at.isoformat() if proj.created_at else None,
        }


def list_projections(
    space_id: str | uuid.UUID | None = None,
    frame_id: str | uuid.UUID | None = None,
) -> list[dict]:
    """List projections, optionally filtered by space or frame."""
    from mkb.db.models import Projection

    with SyncSessionLocal() as session:
        q = session.query(Projection).order_by(Projection.created_at.desc())
        if space_id:
            q = q.filter_by(space_id=uuid.UUID(str(space_id)))
        if frame_id:
            q = q.filter_by(frame_id=uuid.UUID(str(frame_id)))
        projections = q.all()
        return [
            {
                "projection_id": str(p.projection_id),
                "space_id": str(p.space_id),
                "frame_id": str(p.frame_id),
                "status": p.status.value,
                "agent_notes": p.agent_notes,
                "extracted_at": p.extracted_at.isoformat() if p.extracted_at else None,
                "space_version": p.space_version,
            }
            for p in projections
        ]


# ── Feedback ─────────────────────────────────────────────────────


def list_feedback(
    project_id: str | uuid.UUID | None = None,
    status: str | None = None,
) -> list[dict]:
    """List feedback items, optionally filtered by project and/or status."""
    from mkb.feedback.manager import list_feedback as _list

    pid = uuid.UUID(str(project_id)) if project_id else None
    return _list(project_id=pid, status=status)


def review_feedback(
    project_id: str | uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run feedback review on a project — KB agent reviews and resolves open feedback."""
    from mkb.agents.feedback_reviewer import run_feedback_review

    pid = uuid.UUID(str(project_id))
    return run_feedback_review(pid, model=model, verbose=verbose)


def resolve_feedback(
    feedback_id: str | uuid.UUID,
    status: str,
    notes: str,
) -> dict:
    """Manually resolve a feedback item."""
    from mkb.feedback.manager import resolve_feedback as _resolve

    fid = uuid.UUID(str(feedback_id))
    return _resolve(fid, status=status, notes=notes, resolved_by="user")
