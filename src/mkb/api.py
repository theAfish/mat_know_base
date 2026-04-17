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

    return ingest_directory(directory, project_label=label)


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
        from mkb.db.models import Asset, ProjectAsset
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
) -> dict:
    """Run knowledge extraction. If project_id given, extract one project.
    Otherwise extract all pending projects.
    """
    from mkb.agents.extraction import run_extraction, run_extraction_all

    if project_id is not None:
        pid = uuid.UUID(str(project_id))
        return run_extraction(pid, model=model, verbose=verbose)
    return run_extraction_all(model=model, verbose=verbose)


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
                "extracted_at": f.extracted_at.isoformat() if f.extracted_at else None,
                "extraction_summary": f.extraction_summary,
            }
            for f in frames
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
