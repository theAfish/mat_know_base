"""
Content-Addressable Storage ingestion worker.

Scans a local directory for files, computes SHA256, deduplicates,
uploads to MinIO, and registers assets in PostgreSQL.
"""

import hashlib
import logging
import uuid
from pathlib import Path

import magic

from mkb.config import settings
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Asset, BatchAsset, IngestionBatch, ProcessingStatus
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


def ingest_file(path: Path, session, batch_id: uuid.UUID | None = None) -> Asset | None:
    """Ingest a single file. Returns the Asset row (new or existing)."""
    path = path.resolve()
    if not path.is_file():
        logger.warning("Skipping non-file: %s", path)
        return None

    file_hash = sha256_file(path)

    # Dedup check – if hash exists, skip upload
    existing = session.query(Asset).filter_by(sha256=file_hash).first()
    if existing:
        logger.info("Duplicate skipped (hash=%s): %s", file_hash[:12], path.name)
        if batch_id:
            _link_batch(session, batch_id, existing.asset_id)
        return existing

    mime = detect_mime(path)
    data = path.read_bytes()
    size = len(data)

    bucket = settings.s3_bucket_raw
    s3_key = f"{file_hash[:2]}/{file_hash[2:4]}/{file_hash}"

    # Upload to MinIO (CAS path)
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

    if batch_id:
        _link_batch(session, batch_id, asset.asset_id)

    return asset


def _link_batch(session, batch_id: uuid.UUID, asset_id: uuid.UUID) -> None:
    exists = (
        session.query(BatchAsset)
        .filter_by(batch_id=batch_id, asset_id=asset_id)
        .first()
    )
    if not exists:
        session.add(BatchAsset(batch_id=batch_id, asset_id=asset_id))


def ingest_directory(directory: str | Path, batch_label: str | None = None) -> dict:
    """
    Walk a directory and ingest every file.
    Returns a summary dict with counts.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Not a directory: {directory}")

    files = sorted(p for p in directory.rglob("*") if p.is_file())
    logger.info("Found %d files in %s", len(files), directory)

    with SyncSessionLocal() as session:
        batch = IngestionBatch(batch_id=uuid.uuid4(), label=batch_label or directory.name)
        session.add(batch)
        session.flush()

        stats = {"total": len(files), "ingested": 0, "duplicates": 0, "errors": 0}

        for fpath in files:
            try:
                asset = ingest_file(fpath, session, batch.batch_id)
                if asset and asset.status == ProcessingStatus.STORED:
                    stats["ingested"] += 1
                else:
                    stats["duplicates"] += 1
            except Exception:
                logger.exception("Failed to ingest %s", fpath)
                stats["errors"] += 1

        session.commit()
        logger.info("Batch %s complete: %s", batch.batch_id, stats)

    return stats
