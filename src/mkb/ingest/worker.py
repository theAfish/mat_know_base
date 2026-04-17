"""
Content-Addressable Storage ingestion worker.

Scans directories for files, computes SHA256, deduplicates,
uploads to MinIO, and registers assets + research projects in PostgreSQL.
"""

import hashlib
import logging
import uuid
from pathlib import Path

import magic

from mkb.config import settings
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Asset, ProcessedAsset, ProcessingStatus, ProjectAsset, ResearchProject
from mkb.storage.s3 import object_exists, upload_bytes

logger = logging.getLogger(__name__)
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def detect_mime(path: Path) -> str:
    return magic.from_file(str(path), mime=True)


def _ingest_file(path: Path, session, project_id: uuid.UUID) -> tuple[Asset, bool]:
    """Ingest a single file. Returns (Asset, is_new)."""
    path = path.resolve()
    file_hash = sha256_file(path)

    existing = session.query(Asset).filter_by(sha256=file_hash).first()
    if existing:
        _link_project(session, project_id, existing.asset_id)
        return existing, False

    mime = detect_mime(path)
    data = path.read_bytes()
    size = len(data)

    bucket = settings.s3_bucket_raw
    s3_key = f"{file_hash[:2]}/{file_hash[2:4]}/{file_hash}"

    if not object_exists(bucket, s3_key):
        upload_bytes(data, bucket, s3_key)
        logger.info("Uploaded %s -> s3://%s/%s", path.name, bucket, s3_key)

    asset = Asset(
        asset_id=uuid.uuid4(),
        sha256=file_hash,
        filename=path.name,
        mime_type=mime,
        size_bytes=size,
        s3_bucket=bucket,
        s3_key=s3_key,
        status=ProcessingStatus.STORED,
    )
    session.add(asset)
    session.flush()
    _link_project(session, project_id, asset.asset_id)
    return asset, True


def _link_project(session, project_id: uuid.UUID, asset_id: uuid.UUID) -> None:
    exists = (
        session.query(ProjectAsset)
        .filter_by(project_id=project_id, asset_id=asset_id)
        .first()
    )
    if not exists:
        session.add(ProjectAsset(project_id=project_id, asset_id=asset_id))


def _scan_files(directory: Path) -> list[Path]:
    """List all files in a directory recursively, sorted."""
    return sorted(p for p in directory.rglob("*") if p.is_file())


def ingest_directory(directory: str | Path, label: str | None = None) -> dict:
    """Ingest a directory as a research project. Creates or updates the project."""
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Not a directory: {directory}")

    source_path = str(directory)
    files = _scan_files(directory)
    logger.info("Found %d files in %s", len(files), directory)

    with SyncSessionLocal() as session:
        # Find or create research project by source_path
        project = session.query(ResearchProject).filter_by(source_path=source_path).first()
        if not project:
            project = ResearchProject(
                project_id=uuid.uuid4(),
                label=label or directory.name,
                source_path=source_path,
                file_count=0,
            )
            session.add(project)
            session.flush()
        elif label:
            project.label = label

        stats = {"total": len(files), "ingested": 0, "duplicates": 0, "errors": 0,
                 "project_id": str(project.project_id)}

        for fpath in files:
            try:
                asset, is_new = _ingest_file(fpath, session, project.project_id)
                if is_new:
                    stats["ingested"] += 1
                else:
                    stats["duplicates"] += 1
            except Exception:
                logger.exception("Failed to ingest %s", fpath)
                stats["errors"] += 1

        # Update file count
        project.file_count = session.query(ProjectAsset).filter_by(
            project_id=project.project_id
        ).count()
        session.commit()
        logger.info("Project %s (%s) complete: %s", project.project_id, project.label, stats)

    return stats


def sync_project(project_id: uuid.UUID) -> dict:
    """Re-scan a project's source_path and ingest any new files.

    Returns stats about what was found/added.
    """
    with SyncSessionLocal() as session:
        project = session.query(ResearchProject).filter_by(project_id=project_id).first()
        if not project:
            return {"error": f"Project {project_id} not found"}
        if not project.source_path:
            return {"error": f"Project {project_id} has no source_path"}

        source_dir = Path(project.source_path)
        if not source_dir.is_dir():
            return {"error": f"Source path not found: {project.source_path}"}

        # Get currently tracked file hashes
        links = session.query(ProjectAsset).filter_by(project_id=project_id).all()
        existing_asset_ids = {l.asset_id for l in links}
        existing_hashes = set()
        if existing_asset_ids:
            assets = session.query(Asset).filter(Asset.asset_id.in_(existing_asset_ids)).all()
            existing_hashes = {a.sha256 for a in assets}

        files = _scan_files(source_dir)
        stats = {"total_on_disk": len(files), "new_ingested": 0, "already_tracked": 0, "errors": 0}

        for fpath in files:
            try:
                file_hash = sha256_file(fpath)
                if file_hash in existing_hashes:
                    stats["already_tracked"] += 1
                    continue
                asset, is_new = _ingest_file(fpath, session, project_id)
                existing_hashes.add(asset.sha256)
                stats["new_ingested"] += 1
            except Exception:
                logger.exception("Failed to ingest %s", fpath)
                stats["errors"] += 1

        project.file_count = session.query(ProjectAsset).filter_by(
            project_id=project_id
        ).count()
        session.commit()

    return stats


def sync_root(root_dir: str | Path) -> dict:
    """Scan a root directory: each immediate subdirectory becomes a research project.

    New subdirectories are ingested. Existing ones are synced for new files.
    Returns aggregate stats.
    """
    root_dir = Path(root_dir).resolve()
    if not root_dir.is_dir():
        raise FileNotFoundError(f"Not a directory: {root_dir}")

    subdirs = sorted(p for p in root_dir.iterdir() if p.is_dir())
    logger.info("Found %d subdirectories in %s", len(subdirs), root_dir)

    results = []
    for subdir in subdirs:
        source_path = str(subdir.resolve())

        with SyncSessionLocal() as session:
            project = session.query(ResearchProject).filter_by(source_path=source_path).first()

        if project:
            logger.info("Syncing existing project: %s (%s)", project.label, subdir.name)
            result = sync_project(project.project_id)
            result["project_id"] = str(project.project_id)
            result["label"] = project.label
            result["action"] = "synced"
        else:
            logger.info("New project found: %s", subdir.name)
            result = ingest_directory(subdir)
            result["action"] = "created"

        results.append(result)

    return {
        "root_dir": str(root_dir),
        "total_projects": len(results),
        "new_projects": sum(1 for r in results if r.get("action") == "created"),
        "synced_projects": sum(1 for r in results if r.get("action") == "synced"),
        "results": results,
    }
