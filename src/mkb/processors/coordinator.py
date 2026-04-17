"""
Central processing coordinator.
Manages routing to appropriate processors, deduplication, and metadata tracking.
"""

import hashlib
import logging
import uuid
from pathlib import Path

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Asset, ProjectAsset, ProcessedAsset, ProcessingLog, ProcessingType
from mkb.processors.dataframe_processor import CSVProcessor, ExcelProcessor, JSONProcessor
from mkb.processors.image_processor import BasicImageProcessor
from mkb.processors.pdf_processor import PDFProcessor
from mkb.processors.text_processor import TextProcessor
from mkb.storage.s3 import download_bytes, object_exists, upload_bytes

logger = logging.getLogger(__name__)

# Registry of all available processors
PROCESSORS = [
    PDFProcessor(),
    ExcelProcessor(),
    CSVProcessor(),
    JSONProcessor(),
    BasicImageProcessor(),
    TextProcessor(),
]


def _mark_asset_metadata(asset: Asset, update: dict) -> None:
    """Persist processing markers on the raw asset metadata field."""
    metadata = dict(asset.metadata_ or {})
    processing_meta = dict(metadata.get("processing") or {})
    processing_meta.update(update)
    metadata["processing"] = processing_meta
    asset.metadata_ = metadata


def _looks_like_tabular_text(data: bytes, sample_lines: int = 8) -> bool:
    """Heuristic classifier for ambiguous plain text (mainly .txt)."""
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return False

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:sample_lines]
    if len(lines) < 2:
        return False

    delimiters = [",", "\t", ";", "|"]
    for delim in delimiters:
        counts = [line.count(delim) for line in lines]
        if min(counts) >= 1 and max(counts) - min(counts) <= 1:
            return True
    return False


def _select_processor(asset: Asset, raw_data: bytes):
    """Select processor with special handling for ambiguous text files."""
    filename_lower = asset.filename.lower()
    if asset.mime_type.startswith("text/") and filename_lower.endswith(".txt"):
        if _looks_like_tabular_text(raw_data):
            return CSVProcessor()
        return TextProcessor()

    for proc in PROCESSORS:
        if proc.can_process(asset.mime_type, asset.filename):
            return proc
    return None


def _get_project_segment(session, asset_id: uuid.UUID) -> str:
    """Resolve an asset's project folder segment. Falls back to 'unassigned'."""
    link = session.query(ProjectAsset).filter_by(asset_id=asset_id).first()
    return str(link.project_id) if link else "unassigned"


def _default_primary_relpath(result) -> str:
    if result.primary_relpath:
        return result.primary_relpath
    return f"{result.processing_type.value}.{result.output_format}"


def _persist_local_outputs(batch_segment: str, asset_id: uuid.UUID, primary_relpath: str, result) -> str:
    """Write processed outputs to local disk at data/processed/<batch>/<asset>/..."""
    from mkb.config import settings

    asset_dir = Path(settings.processed_local_root) / batch_segment / str(asset_id)
    asset_dir.mkdir(parents=True, exist_ok=True)

    primary_path = asset_dir / primary_relpath
    primary_path.parent.mkdir(parents=True, exist_ok=True)
    primary_path.write_bytes(result.content)

    for relpath, blob in (result.artifacts or {}).items():
        p = asset_dir / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)

    return str(asset_dir)


def _upload_processed_bundle(bucket: str, batch_segment: str, asset_id: uuid.UUID, primary_relpath: str, result) -> str:
    """Upload primary output and artifact files to S3 under batch/asset prefix."""
    prefix = f"{batch_segment}/{asset_id}"
    primary_s3_key = f"{prefix}/{primary_relpath}"
    upload_bytes(result.content, bucket, primary_s3_key)

    for relpath, blob in (result.artifacts or {}).items():
        upload_bytes(blob, bucket, f"{prefix}/{relpath}")

    return primary_s3_key


def _processed_bundle_exists(processed_asset: ProcessedAsset) -> bool:
    """Check whether the processed output still exists both locally and in S3."""
    metadata = processed_asset.conversion_metadata or {}
    local_dir = metadata.get("local_dir")
    primary_relpath = metadata.get("primary_relpath")
    artifact_files = metadata.get("artifact_files") or []

    if not local_dir or not primary_relpath:
        return False

    local_root = Path(local_dir)
    if not (local_root / primary_relpath).exists():
        return False

    for relpath in artifact_files:
        if not (local_root / relpath).exists():
            return False

    if not object_exists(processed_asset.s3_bucket, processed_asset.s3_key):
        return False

    s3_prefix = processed_asset.s3_key.rsplit("/", 1)[0]
    for relpath in artifact_files:
        if not object_exists(processed_asset.s3_bucket, f"{s3_prefix}/{relpath}"):
            return False

    return True


def _repair_existing_processed_asset(
    session,
    asset: Asset,
    processed_asset: ProcessedAsset,
    result,
    project_segment: str,
    primary_relpath: str,
) -> None:
    """Recreate missing processed outputs for an existing identical conversion."""
    local_dir = _persist_local_outputs(project_segment, asset.asset_id, primary_relpath, result)
    s3_key = _upload_processed_bundle(
        processed_asset.s3_bucket,
        project_segment,
        asset.asset_id,
        primary_relpath,
        result,
    )

    updated_meta = dict(result.conversion_metadata or {})
    updated_meta.update(
        {
            "project_id": project_segment,
            "local_dir": local_dir,
            "primary_relpath": primary_relpath,
            "artifact_files": sorted((result.artifacts or {}).keys()),
            "artifact_count": len(result.artifacts or {}),
        }
    )

    processed_asset.s3_key = s3_key
    processed_asset.size_bytes = result.size_bytes
    processed_asset.conversion_metadata = updated_meta
    processed_asset.raw_asset_hash = asset.sha256


def process_asset(asset_id: uuid.UUID) -> dict:
    """
    Process a raw asset and convert it to structured formats.
    
    Returns a dict with status and details:
    {
        "asset_id": uuid,
        "status": "SKIPPED" | "SUCCESS" | "FAILED",
        "processing_type": ProcessingType,
        "processed_asset_id": uuid | None,
        "reason": str,  # For skipped/failed
    }
    """
    with SyncSessionLocal() as session:
        # Fetch the asset
        asset = session.query(Asset).filter_by(asset_id=asset_id).first()
        if not asset:
            return {
                "asset_id": asset_id,
                "status": "FAILED",
                "reason": "Asset not found",
            }
        
        logger.info(f"Processing asset {asset_id}: {asset.filename}")
        
        # Download raw file from S3
        try:
            raw_data = download_bytes(asset.s3_bucket, asset.s3_key)
        except Exception as e:
            logger.error(f"Failed to download raw asset {asset_id}: {e}")
            log_entry = ProcessingLog(
                log_id=uuid.uuid4(),
                asset_id=asset_id,
                processing_type=ProcessingType.UNPROCESSABLE,
                status="FAILED",
                error_message=str(e)
            )
            session.add(log_entry)
            session.commit()
            return {
                "asset_id": asset_id,
                "status": "FAILED",
                "processing_type": ProcessingType.UNPROCESSABLE,
                "reason": f"Failed to download raw asset: {e}",
            }

        processor = _select_processor(asset, raw_data)
        if not processor:
            logger.info(f"No processor found for {asset.filename} ({asset.mime_type})")
            log_entry = ProcessingLog(
                log_id=uuid.uuid4(),
                asset_id=asset_id,
                processing_type=ProcessingType.UNPROCESSABLE,
                status="SKIPPED",
                details={"reason": f"No processor for MIME type: {asset.mime_type}"},
            )
            session.add(log_entry)
            _mark_asset_metadata(
                asset,
                {
                    "last_status": "SKIPPED",
                    "last_processing_type": ProcessingType.UNPROCESSABLE.value,
                    "reason": f"No processor for MIME type: {asset.mime_type}",
                },
            )
            session.commit()

            return {
                "asset_id": asset_id,
                "status": "SKIPPED",
                "processing_type": ProcessingType.UNPROCESSABLE,
                "reason": f"No processor for MIME type: {asset.mime_type}",
            }
        
        # Process the file
        try:
            result = processor.process(raw_data, asset.filename)
            
            if not result.is_success():
                # Processing failed
                logger.error(f"Processing failed for {asset.filename}: {result.error}")
                log_entry = ProcessingLog(
                    log_id=uuid.uuid4(),
                    asset_id=asset_id,
                    processing_type=result.processing_type,
                    status="FAILED",
                    error_message=result.error
                )
                session.add(log_entry)
                _mark_asset_metadata(
                    asset,
                    {
                        "last_status": "FAILED",
                        "last_processing_type": result.processing_type.value,
                        "reason": result.error,
                    },
                )
                session.commit()
                
                return {
                    "asset_id": asset_id,
                    "status": "FAILED",
                    "processing_type": result.processing_type,
                    "reason": result.error,
                }
            
            # Check for idempotency - has this exact conversion been done before?
            processed_sha = result.sha256
            existing = session.query(ProcessedAsset).filter_by(
                asset_id=asset_id,
                processing_type=result.processing_type,
                sha256=processed_sha
            ).first()
            
            if existing:
                project_segment = _get_project_segment(session, asset_id)
                primary_relpath = _default_primary_relpath(result)
                bundle_exists = _processed_bundle_exists(existing)
                if bundle_exists:
                    logger.info(
                        f"Skipping duplicate conversion for {asset.filename} "
                        f"(already have {result.processing_type.value})"
                    )
                else:
                    logger.info(
                        f"Rebuilding missing processed outputs for {asset.filename} "
                        f"({result.processing_type.value})"
                    )
                    _repair_existing_processed_asset(
                        session,
                        asset,
                        existing,
                        result,
                        project_segment,
                        primary_relpath,
                    )
                log_entry = ProcessingLog(
                    log_id=uuid.uuid4(),
                    asset_id=asset_id,
                    processing_type=result.processing_type,
                    status="SKIPPED" if bundle_exists else "SUCCESS",
                    processed_asset_id=existing.processed_asset_id,
                    details={
                        "reason": "Identical conversion already exists",
                        "outputs_rebuilt": not bundle_exists,
                    }
                )
                session.add(log_entry)
                _mark_asset_metadata(
                    asset,
                    {
                        "last_status": "SKIPPED" if bundle_exists else "SUCCESS",
                        "last_processing_type": result.processing_type.value,
                        "reason": "Identical conversion already exists",
                        "last_processed_asset_id": str(existing.processed_asset_id),
                        "last_processed_local_dir": (existing.conversion_metadata or {}).get("local_dir"),
                    },
                )
                session.commit()

                return {
                    "asset_id": asset_id,
                    "status": "SKIPPED" if bundle_exists else "SUCCESS",
                    "processing_type": result.processing_type,
                    "processed_asset_id": existing.processed_asset_id,
                    "reason": "Identical conversion already exists" if bundle_exists else "Missing outputs were rebuilt",
                }

            # Upload processed data to S3 (separate bucket)
            from mkb.config import settings

            processed_bucket = settings.s3_bucket_processed
            project_segment = _get_project_segment(session, asset_id)
            primary_relpath = _default_primary_relpath(result)

            local_dir = _persist_local_outputs(project_segment, asset_id, primary_relpath, result)
            s3_key = _upload_processed_bundle(
                processed_bucket,
                project_segment,
                asset_id,
                primary_relpath,
                result,
            )
            logger.info(
                f"Uploaded processed data: s3://{processed_bucket}/{s3_key} "
                f"({result.size_bytes} bytes)"
            )

            result_meta = dict(result.conversion_metadata or {})
            result_meta.update(
                {
                    "project_id": project_segment,
                    "local_dir": local_dir,
                    "primary_relpath": primary_relpath,
                    "artifact_files": sorted((result.artifacts or {}).keys()),
                    "artifact_count": len(result.artifacts or {}),
                }
            )
            
            # Create ProcessedAsset record
            processed_asset = ProcessedAsset(
                processed_asset_id=uuid.uuid4(),
                asset_id=asset_id,
                processing_type=result.processing_type,
                output_format=result.output_format,
                s3_bucket=processed_bucket,
                s3_key=s3_key,
                sha256=processed_sha,
                size_bytes=result.size_bytes,
                conversion_metadata=result_meta,
                raw_asset_hash=asset.sha256,
            )
            session.add(processed_asset)
            session.flush()
            
            # Log the successful conversion
            log_entry = ProcessingLog(
                log_id=uuid.uuid4(),
                asset_id=asset_id,
                processing_type=result.processing_type,
                status="SUCCESS",
                processed_asset_id=processed_asset.processed_asset_id,
                details={
                    "output_format": result.output_format,
                    "size_bytes": result.size_bytes,
                }
            )
            session.add(log_entry)
            _mark_asset_metadata(
                asset,
                {
                    "last_status": "SUCCESS",
                    "last_processing_type": result.processing_type.value,
                    "last_processed_asset_id": str(processed_asset.processed_asset_id),
                    "last_output_format": result.output_format,
                    "last_processed_local_dir": local_dir,
                },
            )
            session.commit()
            
            logger.info(
                f"Successfully processed {asset.filename} -> "
                f"{result.processing_type.value} ({result.output_format})"
            )
            
            return {
                "asset_id": asset_id,
                "status": "SUCCESS",
                "processing_type": result.processing_type,
                "processed_asset_id": processed_asset.processed_asset_id,
            }
        
        except Exception as e:
            logger.exception(f"Unexpected error processing {asset.filename}: {e}")
            log_entry = ProcessingLog(
                log_id=uuid.uuid4(),
                asset_id=asset_id,
                processing_type=processor.get_processing_type(),
                status="FAILED",
                error_message=f"Unexpected error: {str(e)}"
            )
            session.add(log_entry)
            _mark_asset_metadata(
                asset,
                {
                    "last_status": "FAILED",
                    "last_processing_type": processor.get_processing_type().value,
                    "reason": f"Unexpected error: {str(e)}",
                },
            )
            session.commit()
            
            return {
                "asset_id": asset_id,
                "status": "FAILED",
                "processing_type": processor.get_processing_type(),
                "reason": f"Unexpected error: {e}",
            }


def process_all_pending(limit: int | None = None) -> dict:
    """
    Process all unprocessed assets.
    
    Returns a summary dict with statistics.
    """
    with SyncSessionLocal() as session:
        all_assets = session.query(Asset).order_by(Asset.created_at.asc()).all()
        pending_list = []

        for asset in all_assets:
            processed_rows = session.query(ProcessedAsset).filter_by(asset_id=asset.asset_id).all()
            if not processed_rows:
                pending_list.append(asset)
                continue

            if not any(_processed_bundle_exists(row) for row in processed_rows):
                pending_list.append(asset)

        if limit:
            pending_list = pending_list[:limit]

        logger.info(f"Found {len(pending_list)} unprocessed assets")
        
        stats = {
            "total": len(pending_list),
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "results": []
        }
        
        for asset in pending_list:
            result = process_asset(asset.asset_id)
            stats["results"].append(result)
            
            if result["status"] == "SUCCESS":
                stats["success"] += 1
            elif result["status"] == "SKIPPED":
                stats["skipped"] += 1
            else:
                stats["failed"] += 1
        
        return stats


