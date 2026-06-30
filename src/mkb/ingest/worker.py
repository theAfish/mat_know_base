"""
Content-Addressable Storage ingestion worker.

Scans directories for files, computes SHA256, deduplicates,
uploads to MinIO, and registers assets + research projects in PostgreSQL.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import magic
from sqlalchemy import func

from mkb.config import settings
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Asset, ProcessingStatus, ProjectAsset, ResearchProject
from mkb.storage.s3 import object_exists, upload_bytes

logger = logging.getLogger(__name__)
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB
HASH_QUERY_BATCH_SIZE = 1_000


@dataclass(frozen=True)
class FileFingerprint:
    path: Path
    sha256: str
    size_bytes: int


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def detect_mime(path: Path) -> str:
    return magic.from_file(str(path), mime=True)


def _fingerprint_files(paths: Iterable[Path]) -> list[FileFingerprint]:
    """Hash each file once before any database writes."""
    return [
        FileFingerprint(
            path=path,
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
        )
        for path in paths
    ]


def _load_existing_assets(
    session,
    fingerprints: Iterable[FileFingerprint],
) -> dict[str, Asset]:
    """Fetch matching assets in batches instead of issuing one query per file."""
    hashes = list(dict.fromkeys(fp.sha256 for fp in fingerprints))
    existing: dict[str, Asset] = {}
    for start in range(0, len(hashes), HASH_QUERY_BATCH_SIZE):
        batch = hashes[start:start + HASH_QUERY_BATCH_SIZE]
        assets = session.query(Asset).filter(Asset.sha256.in_(batch)).all()
        existing.update((asset.sha256, asset) for asset in assets)
    return existing


def _ingest_file(
    fingerprint: FileFingerprint,
    session,
    project_id: uuid.UUID,
    existing_by_hash: dict[str, Asset],
) -> tuple[Asset, bool]:
    """Ingest a single file. Returns (Asset, is_new)."""
    path = fingerprint.path.resolve()
    file_hash = fingerprint.sha256

    existing = existing_by_hash.get(file_hash)
    if existing:
        _link_project(session, project_id, existing.asset_id)
        return existing, False

    mime = detect_mime(path)
    data = path.read_bytes()

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
        size_bytes=fingerprint.size_bytes,
        s3_bucket=bucket,
        s3_key=s3_key,
        status=ProcessingStatus.STORED,
    )
    session.add(asset)
    session.flush()
    existing_by_hash[file_hash] = asset
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


def _find_containing_project(
    session, asset_ids: list[uuid.UUID], exclude_project_id: uuid.UUID
) -> uuid.UUID | None:
    """Return the project_id of a project that contains ALL given assets, or None.

    Only considers projects other than *exclude_project_id* (the newly
    created one). If the assets are spread across multiple projects (or
    belong to no other project), returns None.
    """
    if not asset_ids:
        return None

    unique_asset_ids = set(asset_ids)
    matching_projects = (
        session.query(ProjectAsset.project_id)
        .filter(
            ProjectAsset.asset_id.in_(unique_asset_ids),
            ProjectAsset.project_id != exclude_project_id,
        )
        .group_by(ProjectAsset.project_id)
        .having(func.count(func.distinct(ProjectAsset.asset_id)) == len(unique_asset_ids))
        .all()
    )
    if not matching_projects:
        return None

    # Return any single matching project (prefer the oldest / smallest UUID).
    return min(row.project_id for row in matching_projects)


def ingest_directory(
    directory: str | Path,
    label: str | None = None,
    *,
    user_named: bool = False,
) -> dict:
    """Ingest a directory as a research project. Creates or updates the project.

    ``user_named`` records whether the provided label came from the user (as
    opposed to being auto-derived from the directory name). When True, the
    project is marked so it is not auto-renamed from the extracted paper title.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Not a directory: {directory}")

    source_path = str(directory)
    files = _scan_files(directory)
    logger.info("Found %d files in %s", len(files), directory)
    fingerprints = _fingerprint_files(files)

    with SyncSessionLocal() as session:
        # Find or create research project by source_path
        project = session.query(ResearchProject).filter_by(source_path=source_path).first()
        existing_by_hash = _load_existing_assets(session, fingerprints)

        # A repeated upload should reuse the existing project instead of creating
        # another project row that merely points at the same assets.
        if not project and fingerprints and all(
            fp.sha256 in existing_by_hash for fp in fingerprints
        ):
            duplicate_of_id = _find_containing_project(
                session,
                [existing_by_hash[fp.sha256].asset_id for fp in fingerprints],
                uuid.uuid4(),
            )
            if duplicate_of_id is not None:
                logger.info(
                    "Skipped duplicate project %s; reusing %s",
                    directory,
                    duplicate_of_id,
                )
                return {
                    "total": len(files),
                    "ingested": 0,
                    "duplicates": len(files),
                    "errors": 0,
                    "project_id": str(duplicate_of_id),
                    "duplicate_of": str(duplicate_of_id),
                    "project_reused": True,
                }

        if not project:
            project = ResearchProject(
                project_id=uuid.uuid4(),
                label=label or directory.name,
                source_path=source_path,
                file_count=0,
            )
            if user_named and label:
                project.metadata_ = {"user_named": True}
            session.add(project)
            session.flush()
        elif label:
            project.label = label
            if user_named:
                meta = dict(project.metadata_ or {})
                meta["user_named"] = True
                project.metadata_ = meta

        stats = {"total": len(files), "ingested": 0, "duplicates": 0, "errors": 0,
                 "project_id": str(project.project_id)}

        duplicate_asset_ids: list[uuid.UUID] = []

        for fingerprint in fingerprints:
            try:
                asset, is_new = _ingest_file(
                    fingerprint,
                    session,
                    project.project_id,
                    existing_by_hash,
                )
                if is_new:
                    stats["ingested"] += 1
                else:
                    stats["duplicates"] += 1
                    duplicate_asset_ids.append(asset.asset_id)
            except Exception:
                logger.exception("Failed to ingest %s", fingerprint.path)
                stats["errors"] += 1

        # Update file count
        project.file_count = session.query(ProjectAsset).filter_by(
            project_id=project.project_id
        ).count()

        # Detect duplicate projects: if ALL files were already-ingested assets,
        # check whether they all belong to a single existing project.
        if stats["ingested"] == 0 and duplicate_asset_ids:
            duplicate_of_id = _find_containing_project(
                session, duplicate_asset_ids, project.project_id
            )
            if duplicate_of_id is not None:
                meta = dict(project.metadata_ or {})
                meta["duplicate_of"] = str(duplicate_of_id)
                project.metadata_ = meta
                stats["duplicate_of"] = str(duplicate_of_id)
                logger.info(
                    "Project %s marked as duplicate of %s",
                    project.project_id,
                    duplicate_of_id,
                )

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
        existing_asset_ids = {link.asset_id for link in links}
        existing_hashes = set()
        if existing_asset_ids:
            assets = session.query(Asset).filter(Asset.asset_id.in_(existing_asset_ids)).all()
            existing_hashes = {a.sha256 for a in assets}

        files = _scan_files(source_dir)
        fingerprints = _fingerprint_files(files)
        existing_by_hash = _load_existing_assets(session, fingerprints)
        stats = {"total_on_disk": len(files), "new_ingested": 0, "already_tracked": 0, "errors": 0}

        for fingerprint in fingerprints:
            try:
                if fingerprint.sha256 in existing_hashes:
                    stats["already_tracked"] += 1
                    continue
                asset, is_new = _ingest_file(
                    fingerprint,
                    session,
                    project_id,
                    existing_by_hash,
                )
                existing_hashes.add(asset.sha256)
                stats["new_ingested"] += 1
            except Exception:
                logger.exception("Failed to ingest %s", fingerprint.path)
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
