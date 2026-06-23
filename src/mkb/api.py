"""
Primary Python API for the Materials Knowledge Base.

All public functions return plain dicts. This module is the recommended
interface; the CLI is a thin wrapper around these functions.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mkb.config import settings
from mkb.db.engine import SyncSessionLocal, init_db

logger = logging.getLogger(__name__)


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _inspect_manual_processed_dir(
    processed_dir: str | Path,
    primary_file: str | None = None,
) -> dict:
    """Inspect a handmade processed-output directory and describe its bundle."""
    root = Path(processed_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Processed directory not found: {root}")

    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        raise FileNotFoundError(f"No files found in processed directory: {root}")

    if primary_file:
        primary_path = (root / primary_file).resolve()
        if not primary_path.is_file():
            raise FileNotFoundError(f"Primary file not found: {primary_path}")
    else:
        def _priority(path: Path) -> tuple[int, str]:
            suffix = path.suffix.lower()
            if suffix in {".md", ".markdown"}:
                rank = 0
            elif suffix in {".parquet", ".csv", ".tsv"}:
                rank = 1
            elif suffix == ".json":
                rank = 2
            elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                rank = 4
            else:
                rank = 3
            return rank, path.relative_to(root).as_posix()

        primary_path = sorted(files, key=_priority)[0]

    primary_relpath = primary_path.relative_to(root).as_posix()
    primary_bytes = primary_path.read_bytes()

    ext = primary_path.suffix.lower()
    if ext in {".md", ".markdown", ".txt"}:
        processing_type = "MARKDOWN"
    elif ext in {".parquet", ".csv", ".tsv", ".json"}:
        processing_type = "DATAFRAME"
    elif ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        processing_type = "IMAGE"
    else:
        processing_type = "MARKDOWN"

    artifact_files = sorted(
        p.relative_to(root).as_posix()
        for p in files
        if p != primary_path
    )

    bundle_hash = hashlib.sha256()
    bundle_hash.update(primary_bytes)
    for relpath in artifact_files:
        data = (root / relpath).read_bytes()
        bundle_hash.update(relpath.encode("utf-8"))
        bundle_hash.update(_sha256_bytes(data).encode("utf-8"))

    from mkb.db.models import ProcessingType

    return {
        "local_dir": str(root),
        "primary_name": primary_path.name,
        "primary_relpath": primary_relpath,
        "processing_type": ProcessingType(processing_type),
        "output_format": primary_path.suffix.lstrip(".") or "bin",
        "artifact_files": artifact_files,
        "size_bytes": len(primary_bytes),
        "sha256": bundle_hash.hexdigest(),
    }


def _choose_asset_for_manual_output(assets: list, primary_name: str | None = None):
    """Choose the most likely raw asset for a handmade processed bundle."""
    if not assets:
        return None
    if not primary_name:
        return assets[0]

    primary_stem = Path(primary_name).stem.lower()
    for asset in assets:
        if Path(asset.filename).stem.lower() == primary_stem:
            return asset

    for asset in assets:
        asset_stem = Path(asset.filename).stem.lower()
        if primary_stem in asset_stem or asset_stem in primary_stem:
            return asset

    return assets[0]


def _normalize_search_query(query: str) -> list[str]:
    """Split a free-text query into non-empty keyword tokens."""
    return [token.strip() for token in query.split() if token.strip()]


def _matches_search_tokens(*values: Any, tokens: list[str]) -> bool:
    """Return True when every token is present in at least one candidate value."""
    haystacks = [str(value).lower() for value in values if value]
    if not tokens:
        return True
    return all(any(token in haystack for haystack in haystacks) for token in tokens)


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


def ingest(
    directory: str | Path,
    label: str | None = None,
    *,
    user_named: bool = False,
) -> dict:
    """Ingest a single project directory.

    Creates or updates a ResearchProject record keyed on the directory path,
    then ingests any new files found inside it.

    ``user_named`` controls whether the provided label should be treated as a
    user-given name (in which case the project will be marked as such and
    excluded from later automatic renaming during extraction).

    Returns a summary dict with counts (total, ingested, duplicates, errors).
    """
    from mkb.ingest.worker import ingest_directory

    return ingest_directory(directory, label=label, user_named=user_named)


def rename_project(
    project_id: str | uuid.UUID,
    label: str,
    *,
    user_initiated: bool = True,
) -> dict:
    """Rename a research project.

    When ``user_initiated`` is True (the default), records
    ``metadata_["user_named"] = True`` so that the automatic post-extraction
    rename will skip this project. Callers that want to perform an automatic
    rename (e.g. from an extracted paper title) should pass
    ``user_initiated=False`` to leave that flag alone.
    """
    from mkb.db.models import ResearchProject

    pid = uuid.UUID(str(project_id))
    cleaned = (label or "").strip()
    if not cleaned:
        return {"error": "label must not be empty"}

    with SyncSessionLocal() as session:
        project = session.query(ResearchProject).filter_by(project_id=pid).first()
        if not project:
            return {"error": f"Project {project_id} not found"}
        project.label = cleaned
        if user_initiated:
            meta = dict(project.metadata_ or {})
            meta["user_named"] = True
            project.metadata_ = meta
        session.commit()
        return {
            "project_id": str(project.project_id),
            "label": project.label,
            "user_named": bool((project.metadata_ or {}).get("user_named")),
        }


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


def process(project_id: str | uuid.UUID | None = None, progress_callback=None) -> dict:
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
                if progress_callback:
                    progress_callback({"message": f"Starting asset {len(results) + 1}/{len(asset_ids)}", "asset_id": str(aid)})
                r = process_asset(aid, progress_callback=progress_callback)
                results.append(r)
            except Exception as exc:
                results.append({"asset_id": str(aid), "error": str(exc)})
        return {"project_id": str(pid), "assets_processed": len(results), "results": results}

    return process_all_pending(progress_callback=progress_callback)


# ── Extraction ───────────────────────────────────────────────────


def extract(
    project_id: str | uuid.UUID | None = None,
    model: str | None = None,
    verbose: bool = False,
    max_passes: int = 1,
    progress_callback=None,
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
        )
    return run_extraction_all(model=model, verbose=verbose, max_passes=max_passes)


# ── Knowledge Frames ─────────────────────────────────────────────


def get_frame(project_id: str | uuid.UUID) -> dict | None:
    """Get the knowledge frame for a project. Returns None if not found."""
    from mkb.db.models import KnowledgeFrame

    init_db()
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
            "agent_annotations": frame.agent_annotations or {},
            "created_at": frame.created_at.isoformat() if frame.created_at else None,
            "updated_at": frame.updated_at.isoformat() if frame.updated_at else None,
        }


def list_frames(status: str | None = None) -> list[dict]:
    """List all knowledge frames, optionally filtered by status."""
    from mkb.db.models import FrameStatus, KnowledgeFrame

    init_db()
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

    init_db()
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


def list_processed_assets(
    project_id: str | uuid.UUID | None = None,
    limit: int = 100,
) -> list[dict]:
    """List processed outputs, optionally filtered by project."""
    from mkb.db.models import Asset, ProcessedAsset, ProjectAsset

    with SyncSessionLocal() as session:
        q = session.query(ProcessedAsset).order_by(ProcessedAsset.created_at.desc())
        if project_id is not None:
            pid = uuid.UUID(str(project_id))
            links = session.query(ProjectAsset).filter_by(project_id=pid).all()
            asset_ids = [l.asset_id for l in links]
            if not asset_ids:
                return []
            q = q.filter(ProcessedAsset.asset_id.in_(asset_ids))

        rows = q.limit(limit).all()
        result = []
        for row in rows:
            asset = session.query(Asset).filter_by(asset_id=row.asset_id).first()
            meta = row.conversion_metadata or {}
            result.append({
                "processed_asset_id": str(row.processed_asset_id),
                "asset_id": str(row.asset_id),
                "filename": asset.filename if asset else None,
                "processing_type": row.processing_type.value,
                "output_format": row.output_format,
                "s3_key": row.s3_key,
                "local_dir": meta.get("local_dir"),
                "primary_relpath": meta.get("primary_relpath"),
                "artifact_count": meta.get("artifact_count", 0),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })
        return result


def link_manual_processed_data(
    processed_dir: str | Path,
    paper_dir: str | Path | None = None,
    project_id: str | uuid.UUID | None = None,
    asset_id: str | uuid.UUID | None = None,
    primary_file: str | None = None,
    processing_type: str | None = None,
    output_format: str | None = None,
) -> dict:
    """Attach a handmade processed-output folder to an existing project asset.

    This is intended for debugging or backfilling local outputs that were created
    outside the normal processing pipeline.
    """
    from mkb.db.models import Asset, ProcessedAsset, ProcessingLog, ProcessingType, ProjectAsset, ResearchProject

    bundle = _inspect_manual_processed_dir(processed_dir, primary_file=primary_file)
    paper_path = Path(paper_dir).resolve() if paper_dir is not None else None

    if processing_type:
        proc_type = ProcessingType(processing_type.upper())
    else:
        proc_type = bundle["processing_type"]
    out_format = output_format or bundle["output_format"]

    with SyncSessionLocal() as session:
        project = None
        if project_id is not None:
            pid = uuid.UUID(str(project_id))
            project = session.query(ResearchProject).filter_by(project_id=pid).first()
        elif paper_path is not None:
            project = session.query(ResearchProject).filter_by(source_path=str(paper_path)).first()
            if project is None and paper_path.is_dir():
                ingest_result = ingest(paper_path, label=paper_path.name)
                pid = uuid.UUID(ingest_result["project_id"])
                project = session.query(ResearchProject).filter_by(project_id=pid).first()
        elif asset_id is not None:
            # Look up the owning project via ProjectAsset link
            aid = uuid.UUID(str(asset_id))
            link = session.query(ProjectAsset).filter_by(asset_id=aid).first()
            if link is not None:
                project = (
                    session.query(ResearchProject)
                    .filter_by(project_id=link.project_id)
                    .first()
                )

        if not project:
            raise ValueError("Could not find a target project. Provide --paper-dir or --project-id.")

        if asset_id is not None:
            target_asset = session.query(Asset).filter_by(asset_id=uuid.UUID(str(asset_id))).first()
        else:
            links = session.query(ProjectAsset).filter_by(project_id=project.project_id).all()
            asset_ids = [l.asset_id for l in links]
            assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all() if asset_ids else []
            target_asset = _choose_asset_for_manual_output(assets, bundle["primary_name"])

        if not target_asset:
            raise ValueError(
                "No raw asset found for the target project. Ingest the paper folder first or pass --asset-id."
            )

        link = session.query(ProjectAsset).filter_by(
            project_id=project.project_id,
            asset_id=target_asset.asset_id,
        ).first()
        if not link:
            session.add(ProjectAsset(project_id=project.project_id, asset_id=target_asset.asset_id))

        s3_key = f"{project.project_id}/{target_asset.asset_id}/{bundle['primary_relpath']}"

        # Mirror the bundle into the canonical processed-local-root so it survives
        # after any caller-supplied temp directory is cleaned up. The local cache
        # is used by the idempotency check and by downstream readers.
        import shutil

        canonical_root = (
            Path(settings.processed_local_root)
            / str(project.project_id)
            / str(target_asset.asset_id)
        )
        bundle_root = Path(bundle["local_dir"]).resolve()
        if bundle_root != canonical_root.resolve():
            canonical_root.mkdir(parents=True, exist_ok=True)
            for relpath in [bundle["primary_relpath"], *bundle["artifact_files"]]:
                src = bundle_root / relpath
                dst = canonical_root / relpath
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            bundle["local_dir"] = str(canonical_root)
            bundle_root = canonical_root

        # Upload primary file + artifacts to the processed-assets S3 bucket so that
        # downstream consumers (idempotency check, frame extraction, projections,
        # etc.) can fetch the bundle the same way as auto-processed outputs.
        from mkb.storage.s3 import upload_bytes

        upload_bytes(
            (bundle_root / bundle["primary_relpath"]).read_bytes(),
            settings.s3_bucket_processed,
            s3_key,
        )
        for relpath in bundle["artifact_files"]:
            artifact_key = f"{project.project_id}/{target_asset.asset_id}/{relpath}"
            upload_bytes(
                (bundle_root / relpath).read_bytes(),
                settings.s3_bucket_processed,
                artifact_key,
            )

        metadata = {
            "project_id": str(project.project_id),
            "local_dir": bundle["local_dir"],
            "primary_relpath": bundle["primary_relpath"],
            "artifact_files": bundle["artifact_files"],
            "artifact_count": len(bundle["artifact_files"]),
            "linked_via": "debug_manual_link",
            "paper_dir": str(paper_path) if paper_path is not None else None,
        }

        existing = (
            session.query(ProcessedAsset)
            .filter_by(asset_id=target_asset.asset_id, processing_type=proc_type)
            .order_by(ProcessedAsset.created_at.desc())
            .first()
        )

        if existing:
            existing.output_format = out_format
            existing.s3_bucket = settings.s3_bucket_processed
            existing.s3_key = s3_key
            existing.sha256 = bundle["sha256"]
            existing.size_bytes = bundle["size_bytes"]
            existing.conversion_metadata = metadata
            existing.raw_asset_hash = target_asset.sha256
            processed_asset = existing
            action = "updated"
        else:
            processed_asset = ProcessedAsset(
                processed_asset_id=uuid.uuid4(),
                asset_id=target_asset.asset_id,
                processing_type=proc_type,
                output_format=out_format,
                s3_bucket=settings.s3_bucket_processed,
                s3_key=s3_key,
                sha256=bundle["sha256"],
                size_bytes=bundle["size_bytes"],
                conversion_metadata=metadata,
                raw_asset_hash=target_asset.sha256,
            )
            session.add(processed_asset)
            action = "created"

        asset_meta = dict(target_asset.metadata_ or {})
        processing_meta = dict(asset_meta.get("processing") or {})
        processing_meta.update(
            {
                "last_status": "SUCCESS",
                "last_processing_type": proc_type.value,
                "last_output_format": out_format,
                "last_processed_asset_id": str(processed_asset.processed_asset_id),
                "last_processed_local_dir": bundle["local_dir"],
                "source": "manual_debug_link",
            }
        )
        asset_meta["processing"] = processing_meta
        target_asset.metadata_ = asset_meta

        session.add(
            ProcessingLog(
                log_id=uuid.uuid4(),
                asset_id=target_asset.asset_id,
                processing_type=proc_type,
                status="SUCCESS",
                processed_asset_id=processed_asset.processed_asset_id,
                details={
                    "debug": True,
                    "action": action,
                    "primary_relpath": bundle["primary_relpath"],
                    "artifact_count": len(bundle["artifact_files"]),
                },
            )
        )
        session.commit()

        return {
            "status": action,
            "project_id": str(project.project_id),
            "asset_id": str(target_asset.asset_id),
            "processed_asset_id": str(processed_asset.processed_asset_id),
            "processing_type": proc_type.value,
            "output_format": out_format,
            "local_dir": bundle["local_dir"],
            "primary_relpath": bundle["primary_relpath"],
            "artifact_files": bundle["artifact_files"],
        }


def list_projects(limit: int = 50) -> list[dict]:
    """List research projects."""
    from collections import defaultdict

    from mkb.db.models import CanonicalWorkflow, KnowledgeFrame, ProcessedAsset, ProjectAsset, RawWorkflowExtraction, ResearchProject

    init_db()
    with SyncSessionLocal() as session:
        projects = (
            session.query(ResearchProject)
            .order_by(ResearchProject.created_at.desc())
            .limit(limit)
            .all()
        )
        if not projects:
            return []

        project_ids = [p.project_id for p in projects]

        # Bulk-fetch asset links for all queried projects
        all_links = session.query(ProjectAsset).filter(ProjectAsset.project_id.in_(project_ids)).all()
        project_to_asset_ids: dict = defaultdict(list)
        for link in all_links:
            project_to_asset_ids[link.project_id].append(link.asset_id)

        # Bulk-fetch which assets have at least one ProcessedAsset record
        all_asset_ids = [link.asset_id for link in all_links]
        if all_asset_ids:
            processed_ids = {
                row.asset_id
                for row in session.query(ProcessedAsset.asset_id)
                .filter(ProcessedAsset.asset_id.in_(all_asset_ids))
                .distinct()
                .all()
            }
        else:
            processed_ids = set()

        # Bulk-fetch frames
        frames = session.query(KnowledgeFrame).filter(KnowledgeFrame.project_id.in_(project_ids)).all()
        frame_by_project = {f.project_id: f for f in frames}
        workflow_rows = (
            session.query(RawWorkflowExtraction)
            .filter(RawWorkflowExtraction.project_id.in_(project_ids))
            .order_by(RawWorkflowExtraction.version.desc())
            .all()
        )
        workflow_by_project = {}
        for workflow in workflow_rows:
            workflow_by_project.setdefault(workflow.project_id, workflow)
            current = workflow_by_project[workflow.project_id]
            current_is_valid = (
                current.status == "COMPLETED"
                and current.record_status in {"active", "needs_review"}
            )
            if not current_is_valid and workflow.status == "COMPLETED" and workflow.record_status in {"active", "needs_review"}:
                workflow_by_project[workflow.project_id] = workflow
        canonical_rows = (
            session.query(CanonicalWorkflow)
            .filter(CanonicalWorkflow.project_id.in_(project_ids))
            .order_by(CanonicalWorkflow.version.desc()).all()
        )
        canonical_by_project = {}
        for canonical in canonical_rows:
            canonical_by_project.setdefault(canonical.project_id, canonical)

        result = []
        for p in projects:
            asset_ids_for_project = project_to_asset_ids[p.project_id]
            total = len(asset_ids_for_project)
            processed_count = sum(1 for aid in asset_ids_for_project if aid in processed_ids)
            if total == 0:
                processing_status = "NO_ASSETS"
            elif processed_count == 0:
                processing_status = "UNPROCESSED"
            elif processed_count < total:
                processing_status = "PARTIAL"
            else:
                processing_status = "PROCESSED"

            frame = frame_by_project.get(p.project_id)
            workflow = workflow_by_project.get(p.project_id)
            canonical = canonical_by_project.get(p.project_id)
            result.append({
                "project_id": str(p.project_id),
                "label": p.label,
                "source_path": p.source_path,
                "file_count": p.file_count,
                "asset_count": total,
                "processing_status": processing_status,
                "frame_status": frame.status.value if frame else "NO_FRAME",
                "workflow_status": workflow.status if workflow else "NO_WORKFLOW",
                "workflow_version": workflow.version if workflow else None,
                "canonical_workflow_status": canonical.status if canonical else "NO_CANONICAL_WORKFLOW",
                "canonical_workflow_version": canonical.version if canonical else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "duplicate_of": (p.metadata_ or {}).get("duplicate_of"),
                "group_id": str(p.group_id) if p.group_id else None,
            })
        return result


# ── Project groups ─────────────────────────────────────────────


def _serialize_group(g, project_count: int) -> dict:
    return {
        "group_id": str(g.group_id),
        "name": g.name,
        "description": g.description,
        "color": g.color,
        "display_order": g.display_order,
        "project_count": project_count,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "updated_at": g.updated_at.isoformat() if g.updated_at else None,
    }


def list_project_groups() -> list[dict]:
    """List all project groups with project counts."""
    from sqlalchemy import func as sa_func

    from mkb.db.models import ProjectGroup, ResearchProject

    init_db()
    with SyncSessionLocal() as session:
        groups = (
            session.query(ProjectGroup)
            .order_by(ProjectGroup.display_order, ProjectGroup.created_at)
            .all()
        )
        counts = dict(
            session.query(ResearchProject.group_id, sa_func.count())
            .filter(ResearchProject.group_id.isnot(None))
            .group_by(ResearchProject.group_id)
            .all()
        )
        return [_serialize_group(g, counts.get(g.group_id, 0)) for g in groups]


def create_project_group(
    name: str,
    *,
    description: str | None = None,
    color: str | None = None,
    display_order: int | None = None,
) -> dict:
    from mkb.db.models import ProjectGroup

    cleaned = (name or "").strip()
    if not cleaned:
        return {"error": "name must not be empty"}

    init_db()
    with SyncSessionLocal() as session:
        if display_order is None:
            current_max = (
                session.query(ProjectGroup)
                .order_by(ProjectGroup.display_order.desc())
                .first()
            )
            display_order = (current_max.display_order + 1) if current_max else 0
        group = ProjectGroup(
            name=cleaned,
            description=(description or None),
            color=(color or None),
            display_order=int(display_order),
        )
        session.add(group)
        session.commit()
        session.refresh(group)
        return _serialize_group(group, 0)


def update_project_group(
    group_id: str | uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
    display_order: int | None = None,
) -> dict:
    from sqlalchemy import func as sa_func

    from mkb.db.models import ProjectGroup, ResearchProject

    gid = uuid.UUID(str(group_id))
    init_db()
    with SyncSessionLocal() as session:
        group = session.query(ProjectGroup).filter_by(group_id=gid).first()
        if not group:
            return {"error": f"Group {group_id} not found"}
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                return {"error": "name must not be empty"}
            group.name = cleaned
        if description is not None:
            group.description = description.strip() or None
        if color is not None:
            group.color = color.strip() or None
        if display_order is not None:
            group.display_order = int(display_order)
        session.commit()
        session.refresh(group)
        count = (
            session.query(sa_func.count())
            .select_from(ResearchProject)
            .filter(ResearchProject.group_id == gid)
            .scalar()
        ) or 0
        return _serialize_group(group, int(count))


def delete_project_group(group_id: str | uuid.UUID) -> dict:
    """Delete a group. Projects in it are unassigned (group_id set to NULL)."""
    from mkb.db.models import ProjectGroup, ResearchProject

    gid = uuid.UUID(str(group_id))
    init_db()
    with SyncSessionLocal() as session:
        group = session.query(ProjectGroup).filter_by(group_id=gid).first()
        if not group:
            return {"error": f"Group {group_id} not found"}
        unassigned = (
            session.query(ResearchProject)
            .filter(ResearchProject.group_id == gid)
            .update({ResearchProject.group_id: None}, synchronize_session=False)
        )
        session.delete(group)
        session.commit()
        return {"group_id": str(gid), "deleted": True, "unassigned_projects": int(unassigned)}


def delete_project(
    project_id: str | uuid.UUID,
    *,
    delete_s3_objects: bool = True,
) -> dict:
    """Hard-delete a research project and all data exclusively owned by it.

    Cascade:
    - ``ProjectAsset`` links for this project are removed.
    - ``Asset`` / ``ProcessedAsset`` / ``ProcessingLog`` records are removed
      only when the asset is **not** linked to any other project (i.e. not
      shared).  When ``delete_s3_objects`` is True the corresponding S3
      objects are removed before the DB records.
    - ``KnowledgeFrame`` owned by this project is deleted, along with its
      ``ExtractionPass``, all ``Projection`` rows (hard delete), and all
      ``Feedback`` rows whose ``target_frame_id`` / ``target_project_id``
      match.
    - The ``ResearchProject`` record itself is deleted last.

    Returns a summary dict or ``{"error": ...}`` when the project is not found.
    """
    from mkb.db.models import (
        Asset,
        CanonicalWorkflow,
        ExtractionPass,
        Feedback,
        KnowledgeFrame,
        ProcessedAsset,
        ProcessingLog,
        ProjectAsset,
        Projection,
        RawWorkflowExtraction,
        ResearchProject,
        WorkflowIndexEntry,
        WorkflowMaintenanceTask,
    )
    from mkb.storage.s3 import delete_object

    pid = uuid.UUID(str(project_id))
    init_db()

    with SyncSessionLocal() as session:
        project = session.query(ResearchProject).filter_by(project_id=pid).first()
        if not project:
            return {"error": f"Project {project_id} not found"}

        # ── Collect asset IDs linked to this project ──────────────────────
        own_links = session.query(ProjectAsset).filter_by(project_id=pid).all()
        own_asset_ids = [lnk.asset_id for lnk in own_links]

        # Determine which of those assets are shared with other projects
        shared_asset_ids: set[uuid.UUID] = set()
        if own_asset_ids:
            other_links = (
                session.query(ProjectAsset.asset_id)
                .filter(
                    ProjectAsset.asset_id.in_(own_asset_ids),
                    ProjectAsset.project_id != pid,
                )
                .distinct()
                .all()
            )
            shared_asset_ids = {row.asset_id for row in other_links}

        exclusive_asset_ids = [a for a in own_asset_ids if a not in shared_asset_ids]

        # ── Remove S3 objects and DB records for exclusive assets ─────────
        deleted_assets = 0
        deleted_processed = 0
        deleted_s3_objects = 0

        if exclusive_asset_ids:
            # ProcessedAsset rows (and their S3 objects)
            processed_rows = (
                session.query(ProcessedAsset)
                .filter(ProcessedAsset.asset_id.in_(exclusive_asset_ids))
                .all()
            )
            for pa in processed_rows:
                if delete_s3_objects:
                    try:
                        delete_object(pa.s3_bucket, pa.s3_key)
                        deleted_s3_objects += 1
                    except Exception:
                        logger.warning(
                            "Failed to delete S3 object %s/%s", pa.s3_bucket, pa.s3_key
                        )
                session.delete(pa)
            deleted_processed = len(processed_rows)

            # ProcessingLog rows
            session.query(ProcessingLog).filter(
                ProcessingLog.asset_id.in_(exclusive_asset_ids)
            ).delete(synchronize_session=False)

            # Raw asset S3 objects + Asset rows
            raw_assets = (
                session.query(Asset)
                .filter(Asset.asset_id.in_(exclusive_asset_ids))
                .all()
            )
            for asset in raw_assets:
                if delete_s3_objects:
                    try:
                        delete_object(asset.s3_bucket, asset.s3_key)
                        deleted_s3_objects += 1
                    except Exception:
                        logger.warning(
                            "Failed to delete S3 object %s/%s", asset.s3_bucket, asset.s3_key
                        )
                session.delete(asset)
            deleted_assets = len(raw_assets)

        # ── Remove ProjectAsset links (including shared ones) ─────────────
        for lnk in own_links:
            session.delete(lnk)

        # ── Knowledge frame + dependents ──────────────────────────────────
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        deleted_projections = 0
        deleted_passes = 0
        deleted_feedback = 0

        if frame:
            fid = frame.frame_id

            # Projections (hard delete)
            deleted_projections = (
                session.query(Projection)
                .filter(Projection.frame_id == fid)
                .delete(synchronize_session=False)
            )

            # ExtractionPass rows
            deleted_passes = (
                session.query(ExtractionPass)
                .filter(ExtractionPass.frame_id == fid)
                .delete(synchronize_session=False)
            )

            # Feedback rows tied to this frame
            deleted_feedback = (
                session.query(Feedback)
                .filter(Feedback.target_frame_id == fid)
                .delete(synchronize_session=False)
            )

            session.delete(frame)

        # Also remove any feedback rows referencing the project but a different
        # (or NULL) frame (defensive clean-up).
        extra_feedback = (
            session.query(Feedback)
            .filter(Feedback.target_project_id == pid)
            .delete(synchronize_session=False)
        )
        deleted_feedback += extra_feedback

        deleted_workflows = (
            session.query(RawWorkflowExtraction)
            .filter(RawWorkflowExtraction.project_id == pid)
            .delete(synchronize_session=False)
        )
        deleted_canonical_workflows = (
            session.query(CanonicalWorkflow)
            .filter(CanonicalWorkflow.project_id == pid)
            .delete(synchronize_session=False)
        )
        session.query(WorkflowIndexEntry).filter(
            WorkflowIndexEntry.project_id == pid
        ).delete(synchronize_session=False)
        session.query(WorkflowMaintenanceTask).filter(
            WorkflowMaintenanceTask.project_id == pid
        ).delete(synchronize_session=False)

        # ── Delete the project itself ─────────────────────────────────────
        session.delete(project)
        session.commit()

    return {
        "project_id": str(pid),
        "deleted": True,
        "deleted_assets": deleted_assets,
        "shared_assets_kept": len(shared_asset_ids),
        "deleted_processed_assets": deleted_processed,
        "deleted_s3_objects": deleted_s3_objects,
        "deleted_projections": deleted_projections,
        "deleted_extraction_passes": deleted_passes,
        "deleted_feedback": deleted_feedback,
        "deleted_workflow_versions": deleted_workflows,
        "deleted_canonical_workflow_versions": deleted_canonical_workflows,
    }


# ── Raw workflow graphs ───────────────────────────────────────


def extract_raw_workflow(project_id: str | uuid.UUID, model: str | None = None, verbose: bool = False, progress_callback=None) -> dict:
    """Append a new faithful raw-workflow extraction version for a project."""
    from mkb.agents.workflow_extraction import run_workflow_extraction

    readiness = get_raw_workflow_extraction_readiness(project_id)
    if not readiness.get("ready"):
        return {
            "status": "error",
            "message": readiness.get("message") or "Project is not ready for workflow extraction",
        }
    init_db()
    return run_workflow_extraction(
        uuid.UUID(str(project_id)), model=model, verbose=verbose,
        progress_callback=progress_callback,
    )


def get_raw_workflow_extraction_readiness(project_id: str | uuid.UUID) -> dict:
    """Check whether a project has readable sources for raw workflow extraction."""
    from mkb.db.models import (
        ProcessedAsset,
        ProcessingType,
        ProjectAsset,
        RawWorkflowExtraction,
        ResearchProject,
    )

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        project = session.query(ResearchProject).filter_by(project_id=pid).first()
        if not project:
            return {"ready": False, "message": f"Project {pid} not found"}

        rows = (
            session.query(ProcessedAsset.asset_id)
            .join(ProjectAsset, ProjectAsset.asset_id == ProcessedAsset.asset_id)
            .filter(
                ProjectAsset.project_id == pid,
                ProcessedAsset.processing_type == ProcessingType.MARKDOWN,
            )
            .distinct()
            .all()
        )
        asset_ids = [str(row.asset_id) for row in rows]
        if not asset_ids:
            return {
                "ready": False,
                "message": (
                    "Workflow extraction requires processed Markdown, but this project has no readable "
                    "processed Markdown files yet. Run Process first and confirm Markdown outputs exist."
                ),
            }

        unfinished = (
            session.query(RawWorkflowExtraction)
            .filter(
                RawWorkflowExtraction.project_id == pid,
                RawWorkflowExtraction.graph.is_(None),
                RawWorkflowExtraction.status.in_(("IN_PROGRESS", "FAILED")),
            )
            .order_by(RawWorkflowExtraction.version.desc())
            .first()
        )
        return {
            "ready": True,
            "project_id": str(pid),
            "readable_asset_ids": asset_ids,
            "resume_extraction_id": str(unfinished.extraction_id) if unfinished else None,
            "resume_version": unfinished.version if unfinished else None,
            "has_checkpoint": bool(unfinished and unfinished.checkpoint),
        }


def list_raw_workflows(project_id: str | uuid.UUID, include_graph: bool = False) -> list[dict]:
    """List append-only raw workflow versions, newest first."""
    from mkb.db.models import RawWorkflowExtraction

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        rows = (
            session.query(RawWorkflowExtraction)
            .filter(RawWorkflowExtraction.project_id == pid)
            .order_by(RawWorkflowExtraction.version.desc())
            .all()
        )
        return [_serialize_raw_workflow(row, include_graph=include_graph) for row in rows]


def get_raw_workflow(project_id: str | uuid.UUID, version: int | None = None) -> dict | None:
    """Get the latest completed raw workflow, or a specific version."""
    from mkb.db.models import RawWorkflowExtraction

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        query = session.query(RawWorkflowExtraction).filter(RawWorkflowExtraction.project_id == pid)
        if version is None:
            query = query.filter(
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
            ).order_by(RawWorkflowExtraction.version.desc())
        else:
            query = query.filter(RawWorkflowExtraction.version == version)
        row = query.first()
        return _serialize_raw_workflow(row, include_graph=True) if row else None


def delete_raw_workflow_version(project_id: str | uuid.UUID, version: int) -> dict:
    """Delete one unfinished raw workflow version so it cannot be resumed."""
    from mkb.db.models import RawWorkflowExtraction

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        row = (
            session.query(RawWorkflowExtraction)
            .filter(
                RawWorkflowExtraction.project_id == pid,
                RawWorkflowExtraction.version == version,
            )
            .first()
        )
        if not row:
            return {"error": "Raw workflow version not found"}
        if row.status == "COMPLETED" or row.graph is not None:
            return {"error": "Completed raw workflow versions cannot be deleted"}

        extraction_id = row.extraction_id
        session.delete(row)
        session.commit()
        return {
            "status": "deleted",
            "project_id": str(pid),
            "version": version,
            "extraction_id": str(extraction_id),
        }


def _serialize_raw_workflow(row, include_graph: bool) -> dict:
    payload = {
        "extraction_id": str(row.extraction_id), "project_id": str(row.project_id),
        "version": row.version, "schema_version": row.schema_version,
        "extractor_version": row.extractor_version, "model": row.model,
        "status": row.status, "record_status": row.record_status,
        "supersedes_extraction_id": str(row.supersedes_extraction_id) if row.supersedes_extraction_id else None,
        "correction_reason": row.correction_reason,
        "correction_author": row.correction_author,
        "correction_details": row.correction_details or {},
        "review_flags": row.review_flags or [],
        "provenance": row.provenance or {}, "error": row.error,
        "extracted_at": row.extracted_at.isoformat() if row.extracted_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "has_checkpoint": bool(row.checkpoint),
        "checkpoint_summary": (row.checkpoint or {}).get("summary"),
        "checkpoint_updated_at": row.checkpoint_updated_at.isoformat() if row.checkpoint_updated_at else None,
        "resumable": row.graph is None and row.status in {"IN_PROGRESS", "FAILED"},
    }
    if include_graph:
        payload["graph"] = row.graph
    elif row.graph:
        payload["node_count"] = len(row.graph.get("nodes", []))
        payload["edge_count"] = len(row.graph.get("edges", []))
    return payload


def review_raw_workflow(extraction_id: str | uuid.UUID, *, status: str | None = None, author: str = "system") -> dict:
    """Run automatic checks and optionally set a manual lifecycle status."""
    from mkb.db.models import RawWorkflowExtraction
    from mkb.workflows.review import VALID_RECORD_STATUSES, audit_raw_graph

    init_db()
    eid = uuid.UUID(str(extraction_id))
    if status is not None and status not in VALID_RECORD_STATUSES:
        return {"error": f"Invalid record status: {status}"}
    with SyncSessionLocal() as session:
        row = session.query(RawWorkflowExtraction).filter_by(extraction_id=eid).first()
        if not row or not row.graph:
            return {"error": "Completed raw workflow not found"}
        later = session.query(RawWorkflowExtraction).filter(
            RawWorkflowExtraction.project_id == row.project_id,
            RawWorkflowExtraction.version > row.version,
            RawWorkflowExtraction.status == "COMPLETED",
        ).order_by(RawWorkflowExtraction.version.desc()).first()
        flags = audit_raw_graph(row.graph, later_graph=later.graph if later else None)
        row.review_flags = flags
        if status:
            row.record_status = status
        elif flags and row.record_status == "active":
            row.record_status = "needs_review"
        row.provenance = {**(row.provenance or {}), "last_reviewed_by": author}
        session.commit()
        return _serialize_raw_workflow(row, include_graph=False)


def correct_raw_workflow(
    extraction_id: str | uuid.UUID, graph: dict, *, reason: str, author: str,
    affected_nodes: list[str] | None = None, affected_edges: list[str] | None = None,
    evidence: str,
) -> dict:
    """Create a corrected immutable version and supersede the source version."""
    from sqlalchemy import func
    from mkb.db.models import RawWorkflowExtraction
    from mkb.workflows.review import audit_raw_graph, correction_metadata, rebase_graph

    init_db()
    eid = uuid.UUID(str(extraction_id))
    new_id = uuid.uuid4()
    details = correction_metadata(reason, author, affected_nodes or [], affected_edges or [], evidence)
    with SyncSessionLocal() as session:
        source = session.query(RawWorkflowExtraction).filter_by(extraction_id=eid).first()
        if not source or source.status != "COMPLETED":
            return {"error": "Completed source workflow not found"}
        corrected = dict(graph)
        corrected["paper_id"] = str(source.project_id)
        corrected["schema_version"] = source.schema_version
        try:
            corrected = rebase_graph(corrected, new_id)
        except Exception as exc:
            return {"error": f"Corrected graph validation failed: {exc}"}
        version = (session.query(func.max(RawWorkflowExtraction.version)).filter_by(project_id=source.project_id).scalar() or 0) + 1
        flags = audit_raw_graph(corrected)
        row = RawWorkflowExtraction(
            extraction_id=new_id, project_id=source.project_id, version=version,
            schema_version=source.schema_version, extractor_version=source.extractor_version,
            model=source.model, status="COMPLETED",
            record_status="needs_review" if flags else "active",
            supersedes_extraction_id=source.extraction_id, graph=corrected,
            correction_reason=reason, correction_author=author,
            correction_details=details, review_flags=flags,
            provenance={**(source.provenance or {}), "correction_evidence": evidence},
            extracted_at=datetime.now(timezone.utc),
        )
        source.record_status = "superseded"
        session.add(row)
        session.commit()
        return _serialize_raw_workflow(row, include_graph=True)


def canonicalize_workflow(project_id: str | uuid.UUID, raw_extraction_id: str | uuid.UUID | None = None, model: str | None = None, verbose: bool = False, progress_callback=None) -> dict:
    """Create an append-only canonical view from a valid raw workflow."""
    from mkb.agents.workflow_canonicalization import run_workflow_canonicalization

    init_db()
    return run_workflow_canonicalization(
        uuid.UUID(str(project_id)),
        uuid.UUID(str(raw_extraction_id)) if raw_extraction_id else None,
        model=model, verbose=verbose, progress_callback=progress_callback,
    )


def list_canonical_workflows(project_id: str | uuid.UUID, include_graph: bool = False) -> list[dict]:
    from mkb.db.models import CanonicalWorkflow

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        rows = session.query(CanonicalWorkflow).filter_by(project_id=pid).order_by(CanonicalWorkflow.version.desc()).all()
        return [_serialize_canonical_workflow(row, include_graph) for row in rows]


def get_canonical_workflow(project_id: str | uuid.UUID, version: int | None = None) -> dict | None:
    from mkb.db.models import CanonicalWorkflow

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        query = session.query(CanonicalWorkflow).filter(CanonicalWorkflow.project_id == pid)
        query = (
            query.filter(CanonicalWorkflow.status == "COMPLETED").order_by(CanonicalWorkflow.version.desc())
            if version is None else query.filter(CanonicalWorkflow.version == version)
        )
        row = query.first()
        return _serialize_canonical_workflow(row, True) if row else None


def _serialize_canonical_workflow(row, include_graph: bool) -> dict:
    payload = {
        "canonicalization_id": str(row.canonicalization_id), "project_id": str(row.project_id),
        "raw_extraction_id": str(row.raw_extraction_id), "version": row.version,
        "schema_version": row.schema_version, "canonicalizer_version": row.canonicalizer_version,
        "model": row.model, "status": row.status, "provenance": row.provenance or {},
        "error": row.error,
        "canonicalized_at": row.canonicalized_at.isoformat() if row.canonicalized_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "has_checkpoint": bool(row.checkpoint),
        "checkpoint_summary": (row.checkpoint or {}).get("summary"),
        "checkpoint_updated_at": row.checkpoint_updated_at.isoformat() if row.checkpoint_updated_at else None,
        "resumable": row.graph is None and row.status in {"IN_PROGRESS", "FAILED"},
    }
    if include_graph:
        payload["graph"] = row.graph
    elif row.graph:
        payload.update(node_count=len(row.graph.get("nodes", [])), edge_count=len(row.graph.get("edges", [])))
    return payload


def curate_workflow_schema(*, min_support: int = 2, author: str = "schema-curator/1.0") -> list[dict]:
    """Analyze accumulated workflows and persist new evidence-backed proposals."""
    from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction, SchemaProposal, SchemaProposalRevision, WorkflowSchemaVersion
    from mkb.workflows.curator import analyze_canonical_workflows
    from mkb.workflows.schema_library import get_schema_library_payload

    init_db()
    with SyncSessionLocal() as session:
        current = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(WorkflowSchemaVersion.version.desc()).first()
        if not current:
            current = WorkflowSchemaVersion(version=1, name="workflow-schema/1.0", payload=get_schema_library_payload(), created_by="seed")
            session.add(current)
            session.flush()
        rows = session.query(CanonicalWorkflow).filter_by(status="COMPLETED").all()
        workflows = []
        for row in rows:
            raw = session.query(RawWorkflowExtraction).filter_by(extraction_id=row.raw_extraction_id).first()
            workflows.append({"canonicalization_id": str(row.canonicalization_id), "graph": row.graph, "raw_graph": raw.graph if raw else {}})
        generated = analyze_canonical_workflows(workflows, min_support=min_support)
        results = []
        for item in generated:
            duplicate = session.query(SchemaProposal).filter(
                SchemaProposal.status.in_(("pending", "revision_requested")),
                SchemaProposal.proposal_type == item["proposal_type"],
                SchemaProposal.payload == item["payload"],
            ).first()
            if duplicate:
                continue
            rationale = (
                f"Deterministic discovery signal: {item.get('analysis', {}).get('signal', 'unknown')} "
                f"with support from {len(item.get('evidence_workflow_ids', []))} workflows."
            )
            proposal = SchemaProposal(
                **item, rationale=rationale,
                base_schema_version=current.name, created_by=author,
            )
            session.add(proposal)
            session.flush()
            session.add(SchemaProposalRevision(
                proposal_id=proposal.proposal_id, revision_number=1,
                payload=proposal.payload,
                evidence_workflow_ids=proposal.evidence_workflow_ids,
                analysis=proposal.analysis, rationale=proposal.rationale,
                author=author,
                author_type="agent" if "agent" in author else "system",
                change_note="Initial proposal draft",
                validation_errors=[],
            ))
            results.append({**item, "proposal_id": str(proposal.proposal_id), "status": "pending"})
        session.commit()
        return results


def list_schema_proposals(status: str | None = "pending") -> list[dict]:
    from sqlalchemy import func
    from mkb.db.models import SchemaProposal, SchemaProposalRevision

    init_db()
    with SyncSessionLocal() as session:
        query = session.query(SchemaProposal)
        if status:
            query = query.filter_by(status=status)
        rows = query.order_by(SchemaProposal.created_at.desc()).all()
        revision_counts = dict(
            session.query(
                SchemaProposalRevision.proposal_id,
                func.count(SchemaProposalRevision.revision_id),
            ).group_by(SchemaProposalRevision.proposal_id).all()
        )
        return [{
            "proposal_id": str(row.proposal_id), "proposal_type": row.proposal_type,
            "status": row.status, "payload": row.payload,
            "evidence_workflow_ids": row.evidence_workflow_ids, "analysis": row.analysis,
            "base_schema_version": row.base_schema_version, "created_by": row.created_by,
            "rationale": row.rationale,
            "reviewer_notes": row.reviewer_notes,
            "validation_errors": (row.analysis or {}).get("validation_errors", []),
            "revision_count": int(revision_counts.get(row.proposal_id, 0)),
            "reviewed_by": row.reviewed_by,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]


def get_schema_proposal_revisions(proposal_id: str | uuid.UUID) -> list[dict]:
    from mkb.db.models import SchemaProposalRevision

    pid = uuid.UUID(str(proposal_id))
    init_db()
    with SyncSessionLocal() as session:
        rows = session.query(SchemaProposalRevision).filter_by(proposal_id=pid).order_by(
            SchemaProposalRevision.revision_number.desc()
        ).all()
        return [{
            "revision_id": str(row.revision_id),
            "revision_number": row.revision_number,
            "payload": row.payload,
            "evidence_workflow_ids": row.evidence_workflow_ids,
            "analysis": row.analysis,
            "rationale": row.rationale,
            "author": row.author,
            "author_type": row.author_type,
            "change_note": row.change_note,
            "validation_errors": row.validation_errors,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]


def edit_schema_proposal(
    proposal_id: str | uuid.UUID, *, payload: dict,
    evidence_workflow_ids: list[str], rationale: str,
    editor: str, change_note: str,
) -> dict:
    """Save an attributed proposal draft revision and revalidate it."""
    from sqlalchemy import func
    from mkb.db.models import (
        CanonicalWorkflow, RawWorkflowExtraction, SchemaProposal, SchemaProposalRevision,
        WorkflowSchemaVersion,
    )
    from mkb.workflows.curator import validate_proposal

    pid = uuid.UUID(str(proposal_id))
    if not editor.strip() or not change_note.strip():
        return {"error": "editor and change_note are required"}
    try:
        evidence_uuids = [uuid.UUID(value) for value in evidence_workflow_ids]
    except (TypeError, ValueError, AttributeError):
        return {"error": "evidence_workflow_ids must contain canonicalization UUIDs"}
    init_db()
    with SyncSessionLocal() as session:
        row = session.query(SchemaProposal).filter_by(proposal_id=pid).first()
        if not row or row.status not in {"pending", "revision_requested"}:
            return {"error": "Only pending or revision-requested proposals can be edited"}
        current = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        if not current:
            return {"error": "Active schema library not found"}
        known_evidence = {
            str(value) for (value,) in session.query(CanonicalWorkflow.canonicalization_id).filter(
                CanonicalWorkflow.canonicalization_id.in_(evidence_uuids)
            ).all()
        } if evidence_workflow_ids else set()
        if evidence_workflow_ids:
            known_evidence.update({
                str(value) for (value,) in session.query(RawWorkflowExtraction.extraction_id).filter(
                    RawWorkflowExtraction.extraction_id.in_(evidence_uuids)
                ).all()
            })
        errors = validate_proposal(
            row.proposal_type, payload, evidence_workflow_ids, current.payload,
        )
        missing = sorted(set(evidence_workflow_ids) - known_evidence)
        if missing:
            errors.append(f"unknown evidence workflows: {', '.join(missing)}")
        row.payload = payload
        row.evidence_workflow_ids = evidence_workflow_ids
        row.rationale = rationale.strip()
        row.base_schema_version = current.name
        row.analysis = {**(row.analysis or {}), "validation_errors": errors}
        row.status = "pending" if not errors else "revision_requested"
        revision_number = int(
            session.query(func.coalesce(func.max(SchemaProposalRevision.revision_number), 0))
            .filter_by(proposal_id=pid).scalar()
        ) + 1
        session.add(SchemaProposalRevision(
            proposal_id=pid, revision_number=revision_number,
            payload=payload, evidence_workflow_ids=evidence_workflow_ids,
            analysis=row.analysis, rationale=row.rationale,
            author=editor.strip(), author_type="human",
            change_note=change_note.strip(), validation_errors=errors,
        ))
        session.commit()
        return {
            "proposal_id": str(pid), "status": row.status,
            "revision_number": revision_number, "validation_errors": errors,
        }


def get_workflow_schema_status() -> dict:
    """Return global schema and curator queue summary for the frontend."""
    from sqlalchemy import func
    from mkb.db.models import SchemaProposal, WorkflowMaintenanceTask, WorkflowSchemaVersion
    from mkb.workflows.schema_library import get_schema_library_payload

    init_db()
    with SyncSessionLocal() as session:
        active = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        payload = active.payload if active else get_schema_library_payload()
        proposal_counts = dict(
            session.query(SchemaProposal.status, func.count(SchemaProposal.proposal_id))
            .group_by(SchemaProposal.status).all()
        )
        pending_recanonicalizations = session.query(func.count(WorkflowMaintenanceTask.task_id)).filter(
            WorkflowMaintenanceTask.task_type == "recanonicalize",
            WorkflowMaintenanceTask.status == "pending",
        ).scalar() or 0
        return {
            "schema_version": active.name if active else payload["schema_version"],
            "version_number": active.version if active else 1,
            "status": active.status if active else "seed",
            "change_summary": active.change_summary if active else "Built-in seed schema",
            "created_by": active.created_by if active else "system",
            "created_at": active.created_at.isoformat() if active and active.created_at else None,
            "object_schema_count": len(payload.get("object_schemas", {})),
            "operation_template_count": len(payload.get("operation_templates", {})),
            "granularity_relation_count": len(payload.get("granularity_relations", [])),
            "proposal_counts": proposal_counts,
            "pending_recanonicalizations": int(pending_recanonicalizations),
        }


def review_schema_proposal(
    proposal_id: str | uuid.UUID, *, approve: bool | None = None,
    reviewer: str, decision: str | None = None, notes: str = "",
) -> dict:
    """Validate and approve/reject a proposal; approval creates a schema snapshot."""
    from sqlalchemy import func
    from mkb.db.models import (
        CanonicalWorkflow, RawWorkflowExtraction, SchemaProposal, SchemaProposalRevision,
        WorkflowMaintenanceTask, WorkflowSchemaVersion,
    )
    from mkb.workflows.curator import apply_proposal, validate_proposal
    from mkb.workflows.maintenance import recanonicalization_reason_for_proposal

    decision = decision or ("approve" if approve else "reject")
    if decision not in {"approve", "reject", "request_revision"}:
        return {"error": f"Unsupported review decision: {decision}"}
    if not reviewer.strip():
        return {"error": "reviewer is required"}
    if decision == "request_revision" and not notes.strip():
        return {"error": "Revision requests require reviewer notes"}
    init_db()
    with SyncSessionLocal() as session:
        row = session.query(SchemaProposal).filter_by(proposal_id=uuid.UUID(str(proposal_id))).first()
        if not row or row.status not in {"pending", "revision_requested"}:
            return {"error": "Reviewable proposal not found"}
        current = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(WorkflowSchemaVersion.version.desc()).first()
        if not current:
            return {"error": "Schema library is not initialized; run the curator first"}
        errors = validate_proposal(row.proposal_type, row.payload, row.evidence_workflow_ids, current.payload)
        known_evidence = {
            str(value) for (value,) in session.query(CanonicalWorkflow.canonicalization_id).filter(
                CanonicalWorkflow.canonicalization_id.in_([
                    uuid.UUID(value) for value in row.evidence_workflow_ids
                ])
            ).all()
        } if row.evidence_workflow_ids else set()
        if row.evidence_workflow_ids:
            known_evidence.update({
                str(value) for (value,) in session.query(RawWorkflowExtraction.extraction_id).filter(
                    RawWorkflowExtraction.extraction_id.in_([
                        uuid.UUID(value) for value in row.evidence_workflow_ids
                    ])
                ).all()
            })
        missing = sorted(set(row.evidence_workflow_ids) - known_evidence)
        if missing:
            errors.append(f"unknown evidence workflows: {', '.join(missing)}")
        rebased_from = None
        if decision == "approve" and row.base_schema_version != current.name:
            rebased_from = row.base_schema_version
            row.base_schema_version = current.name
            row.analysis = {
                **(row.analysis or {}),
                "rebased_from_schema": rebased_from,
                "rebased_to_schema": current.name,
            }
        if decision == "approve" and errors:
            return {"error": "Schema validation failed", "details": errors}
        row.reviewed_by = reviewer.strip()
        row.reviewer_notes = notes.strip() or None
        row.reviewed_at = datetime.now(timezone.utc)
        revision_number = int(
            session.query(func.coalesce(func.max(SchemaProposalRevision.revision_number), 0))
            .filter_by(proposal_id=row.proposal_id).scalar()
        ) + 1
        session.add(SchemaProposalRevision(
            proposal_id=row.proposal_id, revision_number=revision_number,
            payload=row.payload, evidence_workflow_ids=row.evidence_workflow_ids,
            analysis=row.analysis, rationale=row.rationale,
            author=reviewer.strip(), author_type="human",
            change_note=(
                f"Automatically rebased {rebased_from} to {current.name}. "
                if rebased_from else ""
            ) + f"Review decision: {decision}. {notes.strip()}".strip(),
            validation_errors=errors,
        ))
        if decision in {"reject", "request_revision"}:
            row.status = "rejected" if decision == "reject" else "revision_requested"
            session.commit()
            return {
                "proposal_id": str(row.proposal_id), "status": row.status,
                "revision_number": revision_number,
            }
        next_version = current.version + 1
        next_name = f"workflow-schema/1.{next_version - 1}"
        base_payload = {**current.payload, "schema_version": next_name}
        payload = apply_proposal(base_payload, row.proposal_type, row.payload)
        current.status = "superseded"
        session.add(WorkflowSchemaVersion(
            version=next_version, name=next_name, payload=payload,
            change_summary=f"Applied proposal {row.proposal_id}: {row.proposal_type}",
            created_by=reviewer,
        ))
        row.status = "approved"
        affected = 0
        queues_created = 0
        queues_updated = 0
        duplicate_queues_removed = 0
        completed = session.query(CanonicalWorkflow).filter_by(status="COMPLETED").order_by(
            CanonicalWorkflow.project_id, CanonicalWorkflow.version.desc()
        ).all()
        latest_by_project = {}
        for canonical in completed:
            latest_by_project.setdefault(canonical.project_id, canonical)
        # Every immutable schema snapshot has a new version. Even when a
        # proposal directly cites only a subset, each latest project view is
        # queued so its canonical graph can explicitly target that version.
        for canonical in latest_by_project.values():
            canonical.provenance = {
                **(canonical.provenance or {}),
                "recanonicalization_required": True,
                "target_schema_version": next_name,
            }
            pending_tasks = session.query(WorkflowMaintenanceTask).filter_by(
                project_id=canonical.project_id,
                task_type="recanonicalize",
                status="pending",
            ).order_by(WorkflowMaintenanceTask.created_at).all()
            proposal_ids = [str(row.proposal_id)]
            if pending_tasks:
                task = pending_tasks[0]
                previous_ids = (task.scope or {}).get("schema_proposal_ids", [])
                task.scope = {
                    **(task.scope or {}),
                    "schema_proposal_ids": list(dict.fromkeys([
                        *previous_ids, *proposal_ids,
                    ])),
                }
                task.reason = "schema_version_changed"
                task.source_raw_extraction_id = canonical.raw_extraction_id
                task.source_canonicalization_id = canonical.canonicalization_id
                task.target_schema_version = next_name
                task.requested_by = reviewer.strip()
                for duplicate in pending_tasks[1:]:
                    session.delete(duplicate)
                    duplicate_queues_removed += 1
                queues_updated += 1
            else:
                session.add(WorkflowMaintenanceTask(
                    project_id=canonical.project_id,
                    task_type="recanonicalize",
                    reason=recanonicalization_reason_for_proposal(row.proposal_type),
                    source_raw_extraction_id=canonical.raw_extraction_id,
                    source_canonicalization_id=canonical.canonicalization_id,
                    target_schema_version=next_name,
                    requested_by=reviewer.strip(),
                    scope={"schema_proposal_ids": proposal_ids},
                ))
                queues_created += 1
            affected += 1
        session.commit()
        return {
            "proposal_id": str(row.proposal_id), "status": "approved",
            "schema_version": next_name,
            "rebased_from_schema": rebased_from,
            "recanonicalization_scheduled": affected,
            "queues_created": queues_created,
            "queues_updated": queues_updated,
            "duplicate_queues_removed": duplicate_queues_removed,
        }


def schedule_workflow_reextraction(project_id: str | uuid.UUID, *, reason: str, requested_by: str, scope: dict | None = None, raw_extraction_id: str | uuid.UUID | None = None) -> dict:
    """Queue an approved full or partial re-extraction request."""
    from mkb.db.models import RawWorkflowExtraction, WorkflowMaintenanceTask
    from mkb.workflows.maintenance import validate_reextraction_request

    pid = uuid.UUID(str(project_id))
    validated_scope = validate_reextraction_request(reason, scope)
    init_db()
    with SyncSessionLocal() as session:
        query = session.query(RawWorkflowExtraction).filter(
            RawWorkflowExtraction.project_id == pid,
            RawWorkflowExtraction.status == "COMPLETED",
            RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
        )
        raw = query.filter_by(extraction_id=uuid.UUID(str(raw_extraction_id))).first() if raw_extraction_id else query.order_by(RawWorkflowExtraction.version.desc()).first()
        if not raw:
            return {"error": "No valid raw workflow is available for re-extraction"}
        task = WorkflowMaintenanceTask(
            project_id=pid, task_type="reextract", reason=reason,
            source_raw_extraction_id=raw.extraction_id, scope=validated_scope,
            requested_by=requested_by,
        )
        session.add(task)
        session.commit()
        return {"task_id": str(task.task_id), "status": task.status, "task_type": task.task_type, "scope": task.scope}


def schedule_workflow_recanonicalization(project_id: str | uuid.UUID, *, reason: str = "manual_request", requested_by: str, raw_extraction_id: str | uuid.UUID | None = None, target_schema_version: str | None = None) -> dict:
    from mkb.db.models import RawWorkflowExtraction, WorkflowMaintenanceTask
    from mkb.workflows.maintenance import RECANONICALIZATION_REASONS
    from mkb.workflows.schema_library import get_schema_library_payload

    if reason not in RECANONICALIZATION_REASONS:
        raise ValueError(f"Unsupported recanonicalization reason: {reason}")
    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        query = session.query(RawWorkflowExtraction).filter(
            RawWorkflowExtraction.project_id == pid,
            RawWorkflowExtraction.status == "COMPLETED",
            RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
        )
        raw = query.filter_by(extraction_id=uuid.UUID(str(raw_extraction_id))).first() if raw_extraction_id else query.order_by(RawWorkflowExtraction.version.desc()).first()
        if not raw:
            return {"error": "No valid raw workflow is available for canonicalization"}
        task = WorkflowMaintenanceTask(
            project_id=pid, task_type="recanonicalize", reason=reason,
            source_raw_extraction_id=raw.extraction_id,
            target_schema_version=target_schema_version or get_schema_library_payload()["schema_version"],
            requested_by=requested_by,
        )
        session.add(task)
        session.commit()
        return {"task_id": str(task.task_id), "status": task.status, "task_type": task.task_type}


def list_workflow_maintenance_tasks(*, status: str | None = None, project_id: str | uuid.UUID | None = None) -> list[dict]:
    from mkb.db.models import WorkflowMaintenanceTask

    init_db()
    with SyncSessionLocal() as session:
        query = session.query(WorkflowMaintenanceTask)
        if status:
            query = query.filter_by(status=status)
        if project_id:
            query = query.filter_by(project_id=uuid.UUID(str(project_id)))
        return [{
            "task_id": str(row.task_id), "project_id": str(row.project_id),
            "task_type": row.task_type, "reason": row.reason, "scope": row.scope,
            "status": row.status, "target_schema_version": row.target_schema_version,
            "result": row.result, "error": row.error,
        } for row in query.order_by(WorkflowMaintenanceTask.created_at.desc()).all()]


def run_workflow_maintenance_task(task_id: str | uuid.UUID, *, model: str | None = None, verbose: bool = False, progress_callback=None) -> dict:
    """Execute one queued task, retaining both raw and canonical history."""
    from mkb.agents.workflow_canonicalization import run_workflow_canonicalization
    from mkb.agents.workflow_extraction import run_workflow_extraction
    from mkb.db.models import RawWorkflowExtraction, WorkflowMaintenanceTask

    tid = uuid.UUID(str(task_id))
    init_db()
    with SyncSessionLocal() as session:
        task = session.query(WorkflowMaintenanceTask).filter_by(task_id=tid).first()
        if not task or task.status not in {"pending", "failed"}:
            return {"error": "Pending or failed maintenance task not found"}
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        project_id, task_type, reason = task.project_id, task.task_type, task.reason
        source_raw_id, scope = task.source_raw_extraction_id, task.scope
        target_schema_version = task.target_schema_version
        raw = session.query(RawWorkflowExtraction).filter_by(extraction_id=source_raw_id).first()
        baseline = raw.graph if raw else None
        session.commit()
    try:
        if task_type == "reextract":
            extraction = run_workflow_extraction(
                project_id, model=model, verbose=verbose, progress_callback=progress_callback,
                reextraction_request={
                    "reason": reason, "scope": scope,
                    "source_raw_extraction_id": str(source_raw_id),
                    "baseline_graph": baseline,
                },
            )
            if extraction.get("status") != "completed":
                raise RuntimeError(extraction.get("message") or "Re-extraction failed")
            canonical = run_workflow_canonicalization(
                project_id, uuid.UUID(extraction["extraction_id"]), model=model,
                verbose=verbose, progress_callback=progress_callback,
                recanonicalization_reason="raw_version_changed",
            )
            result = {"extraction": extraction, "canonicalization": canonical}
        else:
            result = run_workflow_canonicalization(
                project_id, source_raw_id, model=model, verbose=verbose,
                progress_callback=progress_callback, recanonicalization_reason=reason,
                target_schema_version=target_schema_version,
            )
        successful = result.get("status") == "completed" or result.get("canonicalization", {}).get("status") == "completed"
        if not successful:
            raise RuntimeError(result.get("message") or "Workflow maintenance failed")
    except Exception as exc:
        with SyncSessionLocal() as session:
            task = session.query(WorkflowMaintenanceTask).filter_by(task_id=tid).first()
            task.status, task.error, task.completed_at = "failed", str(exc), datetime.now(timezone.utc)
            session.commit()
        return {"task_id": str(tid), "status": "failed", "error": str(exc)}
    with SyncSessionLocal() as session:
        task = session.query(WorkflowMaintenanceTask).filter_by(task_id=tid).first()
        task.status, task.result, task.completed_at = "completed", result, datetime.now(timezone.utc)
        session.commit()
    return {"task_id": str(tid), "status": "completed", "result": result}


def run_pending_recanonicalizations(
    *, model: str | None = None, verbose: bool = False, progress_callback=None,
) -> dict:
    """Run all currently pending recanonicalizations as one global batch job."""
    from mkb.db.models import WorkflowMaintenanceTask

    init_db()
    with SyncSessionLocal() as session:
        rows = session.query(WorkflowMaintenanceTask).filter_by(
                task_type="recanonicalize", status="pending",
            ).order_by(WorkflowMaintenanceTask.created_at.desc()).all()
        latest_by_project = {}
        duplicates = []
        for row in rows:
            if row.project_id in latest_by_project:
                duplicates.append((row, latest_by_project[row.project_id]))
            else:
                latest_by_project[row.project_id] = row
        for duplicate, retained in duplicates:
            duplicate.status = "superseded"
            duplicate.result = {
                **(duplicate.result or {}),
                "superseded_by_task_id": str(retained.task_id),
            }
        session.commit()
        task_ids = [row.task_id for row in latest_by_project.values()]
    results = []
    completed = 0
    failed = 0
    for index, task_id in enumerate(task_ids, 1):
        if progress_callback:
            progress_callback({
                "stage": "recanonicalization_batch",
                "message": f"Recanonicalizing project workflow {index}/{len(task_ids)}",
            })
        result = run_workflow_maintenance_task(
            task_id, model=model, verbose=verbose,
            progress_callback=progress_callback,
        )
        results.append(result)
        if result.get("status") == "completed":
            completed += 1
        else:
            failed += 1
    return {
        "status": "completed" if failed == 0 else "completed_with_errors",
        "task_count": len(task_ids), "completed": completed, "failed": failed,
        "duplicate_tasks_coalesced": len(duplicates),
        "results": results,
    }


def rebuild_workflow_indexes(project_id: str | uuid.UUID | None = None) -> dict:
    from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction, WorkflowIndexEntry
    from mkb.workflows.indexing import build_index_entries
    from mkb.workflows.schema_library import get_schema_library_payload

    init_db()
    with SyncSessionLocal() as session:
        query = session.query(CanonicalWorkflow).filter_by(status="COMPLETED")
        if project_id:
            query = query.filter_by(project_id=uuid.UUID(str(project_id)))
        rows = query.all()
        ids = [row.canonicalization_id for row in rows]
        if ids:
            session.query(WorkflowIndexEntry).filter(WorkflowIndexEntry.canonicalization_id.in_(ids)).delete(synchronize_session=False)
        count = 0
        for row in rows:
            raw = session.query(RawWorkflowExtraction).filter_by(extraction_id=row.raw_extraction_id).first()
            schema = get_schema_library_payload(row.schema_version)
            for entry in build_index_entries(row.graph or {}, raw.graph if raw else {}, schema):
                session.add(WorkflowIndexEntry(canonicalization_id=row.canonicalization_id, project_id=row.project_id, **entry))
                count += 1
        session.commit()
        return {"workflows_indexed": len(rows), "entries_created": count}


def search_canonical_workflows(source: str | None = None, operation: str | None = None, target: str | None = None, mode: str = "strict", limit: int = 100) -> list[dict]:
    """Search persisted workflow indexes and return evidence-rich explanations."""
    from mkb.db.models import CanonicalWorkflow, WorkflowIndexEntry
    from mkb.workflows.indexing import QUERY_MODES, match_index_entry, normalize

    legacy_modes = {"exact": "strict", "relaxed": "alias-expanded", "expanded": "granularity-expanded", "summarized": "granularity-expanded"}
    mode = legacy_modes.get(mode, mode)
    if mode not in QUERY_MODES:
        raise ValueError(f"Unsupported query mode: {mode}")
    init_db()
    results = []
    seen_paths = set()
    with SyncSessionLocal() as session:
        completed = session.query(CanonicalWorkflow).filter_by(status="COMPLETED").order_by(CanonicalWorkflow.project_id, CanonicalWorkflow.version.desc()).all()
        latest = {}
        for row in completed:
            latest.setdefault(row.project_id, row)
        rows_by_id = {row.canonicalization_id: row for row in latest.values()}
        entry_query = session.query(WorkflowIndexEntry).filter(
            WorkflowIndexEntry.canonicalization_id.in_(rows_by_id)
        ) if rows_by_id else None
        if entry_query is not None and mode in {"strict", "alias-expanded", "evidence-required"}:
            entry_query = entry_query.filter(WorkflowIndexEntry.index_type == "direct")
            if source:
                entry_query = entry_query.filter(WorkflowIndexEntry.source_label == normalize(source))
            if target:
                entry_query = entry_query.filter(WorkflowIndexEntry.target_label == normalize(target))
            if operation and mode in {"strict", "evidence-required"}:
                entry_query = entry_query.filter(WorkflowIndexEntry.operation_label == normalize(operation))
        entries = entry_query.all() if entry_query is not None else []
        for entry in entries:
            data = {column.name: getattr(entry, column.name) for column in WorkflowIndexEntry.__table__.columns}
            matched, explanation = match_index_entry(data, source=source, operation=operation, target=target, mode=mode)
            if not matched:
                continue
            result_key = (entry.canonicalization_id, tuple(entry.path_node_ids))
            if result_key in seen_paths:
                continue
            seen_paths.add(result_key)
            canonical = rows_by_id[entry.canonicalization_id]
            graph_nodes = {node["node_id"]: node for node in (canonical.graph or {}).get("nodes", [])}
            results.append({
                "project_id": str(entry.project_id),
                "canonicalization_id": str(entry.canonicalization_id),
                "version": canonical.version, "mode": mode,
                "path": [graph_nodes[node_id] for node_id in entry.path_node_ids if node_id in graph_nodes],
                "explanation": explanation,
            })
            if len(results) >= limit:
                break
    return results


def assign_projects_to_group(
    project_ids: list[str | uuid.UUID],
    group_id: str | uuid.UUID | None,
) -> dict:
    """Assign multiple projects to a group, or to no group when ``group_id`` is None."""
    from mkb.db.models import ProjectGroup, ResearchProject

    if not project_ids:
        return {"updated": 0, "group_id": None}

    pids = [uuid.UUID(str(p)) for p in project_ids]
    gid = uuid.UUID(str(group_id)) if group_id else None

    init_db()
    with SyncSessionLocal() as session:
        if gid is not None:
            group = session.query(ProjectGroup).filter_by(group_id=gid).first()
            if not group:
                return {"error": f"Group {group_id} not found"}
        updated = (
            session.query(ResearchProject)
            .filter(ResearchProject.project_id.in_(pids))
            .update({ResearchProject.group_id: gid}, synchronize_session=False)
        )
        session.commit()
        return {"updated": int(updated), "group_id": str(gid) if gid else None}


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


def search_library(
    query: str,
    limit: int = 25,
    project_id: str | uuid.UUID | None = None,
) -> dict:
    """Search projects and assets by keyword.

    Every keyword token must match somewhere in the target record. Projects are
    searched by label and source path. Assets are searched by filename, MIME
    type, and selected metadata fields.
    """
    from sqlalchemy import and_, or_

    from mkb.db.models import Asset, ProjectAsset, ResearchProject

    init_db()
    tokens = [token.lower() for token in _normalize_search_query(query)]
    if not tokens:
        return {
            "query": query,
            "tokens": [],
            "project_id": str(project_id) if project_id is not None else None,
            "projects": [],
            "assets": [],
            "total": 0,
        }

    pid = uuid.UUID(str(project_id)) if project_id is not None else None

    with SyncSessionLocal() as session:
        project_filters = [
            or_(
                ResearchProject.label.ilike(f"%{token}%"),
                ResearchProject.source_path.ilike(f"%{token}%"),
            )
            for token in tokens
        ]
        project_query = session.query(ResearchProject)
        if pid is not None:
            project_query = project_query.filter(ResearchProject.project_id == pid)
        project_rows = (
            project_query
            .filter(and_(*project_filters))
            .order_by(ResearchProject.created_at.desc())
            .limit(limit)
            .all()
        )

        asset_filters = [
            or_(
                Asset.filename.ilike(f"%{token}%"),
                Asset.mime_type.ilike(f"%{token}%"),
                Asset.metadata_["title"].astext.ilike(f"%{token}%"),
                Asset.metadata_["description"].astext.ilike(f"%{token}%"),
                Asset.metadata_["original_path"].astext.ilike(f"%{token}%"),
            )
            for token in tokens
        ]
        asset_query = session.query(Asset, ProjectAsset.project_id).outerjoin(
            ProjectAsset,
            ProjectAsset.asset_id == Asset.asset_id,
        )
        if pid is not None:
            asset_query = asset_query.filter(ProjectAsset.project_id == pid)
        asset_rows = (
            asset_query
            .filter(and_(*asset_filters))
            .order_by(Asset.created_at.desc())
            .limit(limit)
            .all()
        )

        projects = [
            {
                "project_id": str(row.project_id),
                "label": row.label,
                "source_path": row.source_path,
                "file_count": row.file_count,
                "kind": "project",
            }
            for row in project_rows
        ]

        assets = []
        for asset, asset_project_id in asset_rows:
            metadata = asset.metadata_ or {}
            if not _matches_search_tokens(
                asset.filename,
                asset.mime_type,
                metadata.get("title"),
                metadata.get("description"),
                metadata.get("original_path"),
                tokens=tokens,
            ):
                continue

            assets.append(
                {
                    "asset_id": str(asset.asset_id),
                    "project_id": str(asset_project_id) if asset_project_id else None,
                    "filename": asset.filename,
                    "mime_type": asset.mime_type,
                    "size_bytes": asset.size_bytes,
                    "status": asset.status.value,
                    "kind": "asset",
                }
            )

        return {
            "query": query,
            "tokens": tokens,
            "project_id": str(pid) if pid is not None else None,
            "projects": projects,
            "assets": assets[:limit],
            "total": len(projects) + len(assets[:limit]),
        }


# ── Spaces ───────────────────────────────────────────────────────


def create_space(
    name: str,
    domain: str,
    extraction_schema: dict,
    system_prompt: str,
    field_descriptions: dict,
    description: str | None = None,
    purpose: str = "tabular_database",
    review_prompt: str | None = None,
    review_trackable: bool = True,
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
        purpose=purpose,
        review_prompt=review_prompt,
        review_trackable=review_trackable,
    )


def update_space(space_id: str | uuid.UUID, **changes) -> dict:
    """Update fields on an existing space. Bumps version automatically.

    Accepted keys: name, description, extraction_schema, system_prompt,
    field_descriptions, domain, purpose, review_prompt.
    """
    from mkb.spaces.registry import update_space as _update

    return _update(space_id, **changes)


def delete_space(space_id: str | uuid.UUID) -> dict:
    """Delete a space by id."""
    from mkb.spaces.registry import delete_space as _delete

    return _delete(space_id)


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
    progress_callback=None,
    source_type: str = "frame",
) -> dict:
    """Run projection on one or more frames using a space definition.

    Args:
        source_type: ``"frame"`` (default) to project from the curated knowledge
            frame, or ``"markdown"`` to project directly from the processed
            Markdown of the project's papers (no extraction step required).

    If frame_id given, project that specific frame.
    If project_id given, find or auto-create the frame for that project.
    """
    from mkb.agents.projection import run_projection
    from mkb.db.models import KnowledgeFrame

    init_db()
    sid = uuid.UUID(str(space_id))
    source_kind = (source_type or "frame").strip().lower()

    if frame_id:
        fid = uuid.UUID(str(frame_id))
        return run_projection(
            sid, fid, model=model, verbose=verbose,
            progress_callback=progress_callback, source_type=source_kind,
        )

    if project_id:
        pid = uuid.UUID(str(project_id))
        if source_kind == "markdown":
            # Frame may not exist yet; the agent runner will create one.
            return run_projection(
                sid, None, model=model, verbose=verbose,
                progress_callback=progress_callback,
                source_type=source_kind, project_id=pid,
            )
        with SyncSessionLocal() as session:
            frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
            if not frame:
                return {"error": f"No frame for project {project_id}"}
            fid = frame.frame_id
        return run_projection(
            sid, fid, model=model, verbose=verbose,
            progress_callback=progress_callback, source_type=source_kind,
        )

    return {"error": "Must specify frame_id or project_id"}


def project_all(
    space_id: str | uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    source_type: str = "frame",
) -> dict:
    """Run projection on all completed frames (or all projects) using a space."""
    from mkb.agents.projection import run_projection_all

    init_db()
    sid = uuid.UUID(str(space_id))
    return run_projection_all(sid, model=model, verbose=verbose, source_type=source_type)


def get_projection(projection_id: str | uuid.UUID) -> dict | None:
    """Get a projection by ID."""
    from mkb.db.models import KnowledgeFrame, Projection, Space
    from mkb.spaces.schema_utils import normalize_projection_data

    init_db()
    pid = uuid.UUID(str(projection_id))
    with SyncSessionLocal() as session:
        proj = session.query(Projection).filter_by(projection_id=pid).first()
        if not proj:
            return None
        frame = session.query(KnowledgeFrame).filter_by(frame_id=proj.frame_id).first()
        space = session.query(Space).filter_by(space_id=proj.space_id).first()
        normalized_data, normalized_validation = normalize_projection_data(
            proj.data or {},
            space.extraction_schema if space else {},
        )

        validation_result = proj.validation_result or {}
        if normalized_validation:
            validation_result = {
                **normalized_validation,
                **validation_result,
            }

        return {
            "projection_id": str(proj.projection_id),
            "space_id": str(proj.space_id),
            "frame_id": str(proj.frame_id),
            "project_id": str(frame.project_id) if frame else None,
            "status": proj.status.value,
            "data": normalized_data,
            "validation_result": validation_result or None,
            "agent_notes": proj.agent_notes,
            "extracted_at": proj.extracted_at.isoformat() if proj.extracted_at else None,
            "space_version": proj.space_version,
            "source_type": getattr(proj, "source_type", "frame"),
            "times_reviewed": proj.times_reviewed,
            "review_notes": proj.review_notes,
            "reviewed_at": proj.reviewed_at.isoformat() if proj.reviewed_at else None,
            "created_at": proj.created_at.isoformat() if proj.created_at else None,
        }


def delete_projection(projection_id: str | uuid.UUID) -> bool:
    """Soft-delete a projection by setting deleted_at.  Returns True if found."""
    from datetime import datetime, timezone

    from mkb.db.models import Projection

    init_db()
    pid = uuid.UUID(str(projection_id))
    with SyncSessionLocal() as session:
        proj = session.query(Projection).filter_by(projection_id=pid).first()
        if not proj:
            return False
        proj.deleted_at = datetime.now(timezone.utc)
        session.commit()
        return True


def list_projections(
    space_id: str | uuid.UUID | None = None,
    frame_id: str | uuid.UUID | None = None,
    project_id: str | uuid.UUID | None = None,
    include_data: bool = False,
    newest_only: bool = False,
    include_history: bool = False,
) -> list[dict]:
    """List projections, optionally filtered by space, frame, or project.

    Args:
        include_history: When False (default), superseded projections (those
            replaced by a tracked review) are hidden. When True, the full
            history is returned, including ``superseded_by_id`` /
            ``supersedes_ids`` pointers so callers can rebuild the chain.
    """
    from mkb.db.models import KnowledgeFrame, Projection, Space
    from mkb.spaces.schema_utils import normalize_projection_data

    init_db()
    with SyncSessionLocal() as session:
        q = (
            session.query(Projection, KnowledgeFrame.project_id)
            .outerjoin(KnowledgeFrame, Projection.frame_id == KnowledgeFrame.frame_id)
            .filter(Projection.deleted_at.is_(None))
            .order_by(Projection.created_at.desc(), Projection.extracted_at.desc())
        )
        if not include_history:
            q = q.filter(Projection.superseded_by_id.is_(None))
        if space_id:
            q = q.filter(Projection.space_id == uuid.UUID(str(space_id)))
        if frame_id:
            q = q.filter(Projection.frame_id == uuid.UUID(str(frame_id)))
        if project_id:
            q = q.filter(KnowledgeFrame.project_id == uuid.UUID(str(project_id)))
        projections = q.all()

        results = []
        seen_keys: set[tuple[str, str]] = set()
        for projection, projection_project_id in projections:
            project_value = str(projection_project_id) if projection_project_id else None
            dedupe_key = (str(projection.space_id), project_value or str(projection.frame_id))
            if newest_only and dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            item = {
                "projection_id": str(projection.projection_id),
                "space_id": str(projection.space_id),
                "frame_id": str(projection.frame_id),
                "project_id": project_value,
                "status": projection.status.value,
                "agent_notes": projection.agent_notes,
                "extracted_at": projection.extracted_at.isoformat() if projection.extracted_at else None,
                "created_at": projection.created_at.isoformat() if projection.created_at else None,
                "space_version": projection.space_version,
                "source_type": getattr(projection, "source_type", "frame"),
                "times_reviewed": projection.times_reviewed,
                "review_notes": projection.review_notes,
                "reviewed_at": projection.reviewed_at.isoformat() if projection.reviewed_at else None,
                "superseded_by_id": (
                    str(projection.superseded_by_id)
                    if getattr(projection, "superseded_by_id", None)
                    else None
                ),
                "supersedes_ids": getattr(projection, "supersedes_ids", None),
            }
            if include_data:
                space = session.query(Space).filter_by(space_id=projection.space_id).first()
                normalized_data, _ = normalize_projection_data(
                    projection.data or {},
                    space.extraction_schema if space else {},
                )
                item["data"] = normalized_data
            results.append(item)

        return results


# ── Projection Exports ──────────────────────────────────────────


def _serialize_projection_payload(
    projection_id: uuid.UUID,
    out_path: Path,
    format: str,
) -> Path:
    """Generic single-projection dump (used for non-qa_benchmark spaces)."""
    import json as _json

    from mkb.db.models import KnowledgeFrame, Projection, Space

    with SyncSessionLocal() as session:
        proj = session.query(Projection).filter_by(projection_id=projection_id).first()
        if not proj:
            raise ValueError(f"Projection {projection_id} not found")
        frame = session.query(KnowledgeFrame).filter_by(frame_id=proj.frame_id).first()
        space = session.query(Space).filter_by(space_id=proj.space_id).first()
        record = {
            "projection_id": str(proj.projection_id),
            "space": {
                "space_id": str(proj.space_id),
                "name": space.name if space else None,
                "purpose": getattr(space, "purpose", None) if space else None,
                "version": proj.space_version,
            },
            "frame_id": str(proj.frame_id) if proj.frame_id else None,
            "project_id": str(frame.project_id) if frame else None,
            "status": proj.status.value,
            "source_type": getattr(proj, "source_type", "frame"),
            "extracted_at": proj.extracted_at.isoformat() if proj.extracted_at else None,
            "agent_notes": proj.agent_notes,
            "data": proj.data or {},
        }

    fmt = (format or "yaml").strip().lower()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        out_path.write_text(_json.dumps(record, indent=2, ensure_ascii=False, default=str))
    else:
        import yaml as _yaml
        out_path.write_text(
            _yaml.safe_dump(record, sort_keys=False, allow_unicode=True)
        )
    return out_path


def export_projection(
    projection_id: str | uuid.UUID,
    out_dir: str | Path,
    format: str = "yaml",
    overwrite: bool = False,
) -> dict:
    """Export a single projection to disk.

    For ``qa_benchmark`` spaces, delegates to the mat_agent_bench exporter
    (one YAML per question under ``<out_dir>/<capability>/<id>.yaml``).
    For all other purposes, writes a single ``<projection_id>.<ext>`` file
    containing the projection payload + metadata.
    """
    from mkb.db.models import Projection, Space
    from mkb.spaces.export_qa_bench import (
        QABenchExportError,
        export_projection_to_yaml,
    )

    init_db()
    pid = uuid.UUID(str(projection_id))
    out_root = Path(out_dir)
    fmt = (format or "yaml").strip().lower()
    if fmt not in {"yaml", "json"}:
        raise ValueError(f"Unsupported export format: {format}")

    with SyncSessionLocal() as session:
        proj = session.query(Projection).filter_by(projection_id=pid).first()
        if not proj:
            return {"error": f"Projection {projection_id} not found"}
        space = session.query(Space).filter_by(space_id=proj.space_id).first()
        purpose = getattr(space, "purpose", None) if space else None

    if purpose == "qa_benchmark" and fmt == "yaml":
        try:
            return export_projection_to_yaml(pid, out_root, overwrite=overwrite)
        except QABenchExportError as e:
            return {"error": str(e)}

    out_path = out_root / f"{pid}.{fmt}"
    if out_path.exists() and not overwrite:
        return {"error": f"{out_path} already exists (pass overwrite=True)"}
    _serialize_projection_payload(pid, out_path, fmt)
    return {"files": [str(out_path)], "skipped": [], "warnings": []}


def export_space_projections(
    space_id_or_name: str | uuid.UUID,
    out_dir: str | Path,
    format: str = "yaml",
    overwrite: bool = False,
    newest_only: bool = True,
) -> dict:
    """Export every projection belonging to a space.

    For ``qa_benchmark`` spaces and ``format='yaml'`` this delegates to the
    aggregated mat_agent_bench exporter. Otherwise one file is written per
    projection: ``<out_dir>/<projection_id>.<ext>``.
    """
    from mkb.db.models import Projection, ProjectionStatus, Space
    from mkb.spaces.export_qa_bench import (
        QABenchExportError,
        export_space_to_yaml,
    )

    init_db()
    out_root = Path(out_dir)
    fmt = (format or "yaml").strip().lower()
    if fmt not in {"yaml", "json"}:
        raise ValueError(f"Unsupported export format: {format}")

    with SyncSessionLocal() as session:
        try:
            sid = uuid.UUID(str(space_id_or_name))
            space = session.query(Space).filter_by(space_id=sid).first()
        except (ValueError, AttributeError):
            space = session.query(Space).filter_by(name=str(space_id_or_name)).first()
        if not space:
            return {"error": f"Space {space_id_or_name} not found"}
        purpose = getattr(space, "purpose", None)
        space_id = space.space_id

    if purpose == "qa_benchmark" and fmt == "yaml":
        try:
            return export_space_to_yaml(space_id, out_root, overwrite=overwrite)
        except QABenchExportError as e:
            return {"error": str(e)}

    # Generic per-projection dump
    with SyncSessionLocal() as session:
        q = (
            session.query(Projection)
            .filter(Projection.space_id == space_id)
            .filter(Projection.deleted_at.is_(None))
            .filter(Projection.status == ProjectionStatus.COMPLETED)
            .order_by(Projection.created_at.desc())
        )
        projections = q.all()
        if newest_only:
            seen: set[str] = set()
            unique = []
            for p in projections:
                key = str(p.frame_id)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(p)
            projections = unique
        ids = [p.projection_id for p in projections]

    written: list[str] = []
    skipped: list[dict] = []
    out_root.mkdir(parents=True, exist_ok=True)
    for pid in ids:
        out_path = out_root / f"{pid}.{fmt}"
        if out_path.exists() and not overwrite:
            skipped.append({"id": str(pid), "reason": "exists"})
            continue
        _serialize_projection_payload(pid, out_path, fmt)
        written.append(str(out_path))

    return {"files": written, "skipped": skipped, "warnings": []}


# ── Knowledge Graphs ────────────────────────────────────────────

def clear_knowledge_graphs(
    project_id: str | uuid.UUID | None = None,
    remove_legacy_frame_sections: bool = True,
) -> dict:
    """Delete (soft-delete) old KG projections and optionally purge legacy frame graph sections."""
    from mkb.knowledge_graph import clear_knowledge_graph_projections, purge_legacy_graph_sections

    init_db()
    pid = uuid.UUID(str(project_id)) if project_id else None
    deleted = clear_knowledge_graph_projections(project_id=pid, include_legacy_spaces=True)

    purged = {"updated_frames": 0, "project_id": str(pid) if pid else None}
    if remove_legacy_frame_sections:
        purged = purge_legacy_graph_sections(project_id=pid)

    return {
        "deleted_projections": deleted,
        "purged_legacy_frame_sections": purged,
    }


def extract_knowledge_graph(
    project_id: str | uuid.UUID | None = None,
    frame_id: str | uuid.UUID | None = None,
    model: str | None = None,
    verbose: bool = False,
    clear_existing: bool = True,
    clear_legacy_frame_sections: bool = True,
    progress_callback=None,
) -> dict:
    """Run concept-graph extraction using one global cross-domain space.

    If frame_id is provided, extract for that frame.
    If project_id is provided, resolve that project's frame and extract.
    Otherwise run for all completed frames.
    """
    from mkb.agents.knowledge_graph import run_knowledge_graph, run_knowledge_graph_all
    from mkb.db.models import KnowledgeFrame
    from mkb.knowledge_graph import ensure_global_kg_space_id, purge_legacy_graph_sections

    init_db()

    if clear_legacy_frame_sections:
        if project_id:
            purge_legacy_graph_sections(project_id=uuid.UUID(str(project_id)))
        else:
            purge_legacy_graph_sections()

    if frame_id is not None:
        fid = uuid.UUID(str(frame_id))
        result = run_knowledge_graph(
            fid,
            model=model,
            verbose=verbose,
            clear_existing=clear_existing,
            progress_callback=progress_callback,
        )
    elif project_id is not None:
        pid = uuid.UUID(str(project_id))
        with SyncSessionLocal() as session:
            frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
            if not frame:
                return {"error": f"No frame for project {project_id}"}
            fid = frame.frame_id
        result = run_knowledge_graph(
            fid,
            model=model,
            verbose=verbose,
            clear_existing=clear_existing,
            progress_callback=progress_callback,
        )
    else:
        result = run_knowledge_graph_all(model=model, verbose=verbose, clear_existing=clear_existing)

    return {
        "global_space_id": str(ensure_global_kg_space_id()),
        **result,
    }


def get_knowledge_graph(
    project_id: str | uuid.UUID | None = None,
) -> dict:
    """Get the merged concept graph from the singleton global KG space."""
    from mkb.agents.tools.knowledge_graph import normalize_knowledge_graph_payload
    from mkb.db.models import KnowledgeFrame, Projection, ProjectionStatus
    from mkb.knowledge_graph import ensure_global_kg_space_id

    init_db()
    sid = ensure_global_kg_space_id()

    with SyncSessionLocal() as session:
        q = (
            session.query(Projection)
            .filter(Projection.space_id == sid)
            .filter(Projection.deleted_at.is_(None))
            .filter(Projection.status.in_([ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]))
        )
        if project_id:
            pid = uuid.UUID(str(project_id))
            q = q.join(KnowledgeFrame, Projection.frame_id == KnowledgeFrame.frame_id)
            q = q.filter(KnowledgeFrame.project_id == pid)
        rows = q.all()

    aggregate = {"concepts": [], "relations": []}
    for row in rows:
        payload, _ = normalize_knowledge_graph_payload(row.data or {})
        aggregate["concepts"].extend(payload["concepts"])
        aggregate["relations"].extend(payload["relations"])

    merged, validation = normalize_knowledge_graph_payload(aggregate)
    return {
        "space_id": str(sid),
        "projection_count": len(rows),
        "graph": merged,
        "validation": validation,
        "project_id": str(project_id) if project_id else None,
    }


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
    progress_callback=None,
) -> dict:
    """Run feedback review on a project — KB agent reviews and resolves open feedback."""
    from mkb.agents.feedback_reviewer import run_feedback_review

    pid = uuid.UUID(str(project_id))
    if progress_callback:
        progress_callback({"message": f"Reviewing feedback for project {str(pid)[:8]}"})
    return run_feedback_review(pid, model=model, verbose=verbose)


def review_projections(
    space_id: str | uuid.UUID,
    project_id: str | uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
) -> dict:
    """Run projection review — consolidate and correct all projections for a project.

    A strict reviewer agent compares all projection runs, cross-references
    against the knowledge frame and source material, and produces a single
    reviewed (consolidated, corrected) projection.
    """
    from mkb.agents.projection_reviewer import run_projection_review

    init_db()
    sid = uuid.UUID(str(space_id))
    pid = uuid.UUID(str(project_id))
    if progress_callback:
        progress_callback({"message": f"Reviewing projections for project {str(pid)[:8]}"})
    return run_projection_review(sid, pid, model=model, verbose=verbose)


def review_projections_all(
    space_id: str | uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    project_ids: list[str] | None = None,
) -> dict:
    """Run projection review on projects in a space.

    Args:
        project_ids: Restrict to these projects (per-project, separate
            sessions). When None, reviews every project that has at least
            one completed projection in the space.
    """
    from mkb.agents.projection_reviewer import run_projection_review_all

    init_db()
    sid = uuid.UUID(str(space_id))
    pids = [uuid.UUID(str(p)) for p in project_ids] if project_ids else None
    return run_projection_review_all(
        sid,
        model=model,
        verbose=verbose,
        progress_callback=progress_callback,
        project_ids=pids,
    )


def review_projections_session(
    space_id: str | uuid.UUID,
    project_ids: list[str],
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
) -> dict:
    """Run a SINGLE reviewer session over multiple selected projects.

    One agent context sees every selected project's projections in turn
    and saves each reviewed result before moving on to the next.
    """
    from mkb.agents.projection_reviewer import run_projection_review_session

    init_db()
    sid = uuid.UUID(str(space_id))
    pids = [uuid.UUID(str(p)) for p in project_ids]
    if not pids:
        return {"status": "error", "message": "project_ids is required for session mode"}
    return run_projection_review_session(
        sid,
        pids,
        model=model,
        verbose=verbose,
        progress_callback=progress_callback,
    )


def review_knowledge_graph(
    mode: str = "auto",
    model: str | None = None,
    verbose: bool = False,
    seed_count: int = 10,
    progress_callback=None,
) -> dict:
    """Run the graph review agent to deduplicate and clean the knowledge graph.

    Two modes:
    - "global": analyzes relation name distributions, standardizes naming, merges
      duplicate or synonymous concept nodes across the entire graph.
    - "local": selects the least-reviewed concepts as starting points, explores
      their neighborhoods, verifies against source frames, and fixes local issues.
    - "auto" (default): randomly picks global or local each time.

    After each run, the times_examined and times_modified counters on each visited
    graph element are incremented in the graph_element_reviews table.

    Args:
        mode: "global", "local", or "auto".
        model: LLM model override.
        verbose: Enable verbose logging.
        seed_count: Number of starting concepts for local mode.
    """
    from mkb.agents.graph_review import run_graph_review

    init_db()
    return run_graph_review(mode=mode, model=model, verbose=verbose, seed_count=seed_count, progress_callback=progress_callback)


def get_graph_review_counts(space_id: str | uuid.UUID | None = None) -> dict:
    """Return review counts (times_examined, times_modified) per graph element.

    Returns a dict with two sub-dicts keyed by normalized element key:
    - ``concepts``: mapping of normalized concept label → {times_examined, times_modified}
    - ``relations``: mapping of "src||rel||tgt" → {times_examined, times_modified}
    """
    from mkb.db.models import GraphElementReview
    from mkb.knowledge_graph import ensure_global_kg_space_id

    init_db()
    sid = uuid.UUID(str(space_id)) if space_id else ensure_global_kg_space_id()

    concepts: dict[str, dict] = {}
    relations: dict[str, dict] = {}

    with SyncSessionLocal() as session:
        rows = session.query(GraphElementReview).filter_by(space_id=sid).all()
        for row in rows:
            entry = {
                "times_examined": row.times_examined,
                "times_modified": row.times_modified,
                "last_examined_at": row.last_examined_at.isoformat() if row.last_examined_at else None,
                "last_modified_at": row.last_modified_at.isoformat() if row.last_modified_at else None,
            }
            if row.element_type == "concept":
                concepts[row.element_key] = entry
            elif row.element_type == "relation":
                relations[row.element_key] = entry

    return {"space_id": str(sid), "concepts": concepts, "relations": relations}


def get_feedback_summary(
    project_id: str | uuid.UUID,
) -> dict:
    """Get counts of feedback by category and status for a project."""
    from mkb.feedback.manager import get_feedback_summary as _summary

    pid = uuid.UUID(str(project_id))
    return _summary(pid)


def resolve_feedback(
    feedback_id: str | uuid.UUID,
    status: str,
    notes: str,
) -> dict:
    """Manually resolve a feedback item."""
    from mkb.feedback.manager import resolve_feedback as _resolve

    fid = uuid.UUID(str(feedback_id))
    return _resolve(fid, status=status, notes=notes, resolved_by="user")
