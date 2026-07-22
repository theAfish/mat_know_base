"""Dry-run-first retention and cross-store consistency checks."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from mkb.ports import Database, ObjectStore

def _older_than(path: Path, cutoff: datetime) -> bool:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff


def retention_plan(*, older_than_days: int = 7, roots: list[Path] | None = None) -> dict[str, Any]:
    """Return safe, bounded local cleanup candidates without deleting anything."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, older_than_days))
    selected_roots = roots or [
        Path("data/uploads/_temp"), Path("data/temp"), Path("data/exports"), Path("logs")
    ]
    items: list[dict[str, Any]] = []
    for root in selected_roots:
        if not root.exists():
            continue
        # Cleanup units are children, never a configured root itself.
        for child in root.iterdir():
            try:
                if _older_than(child, cutoff):
                    size = sum(p.stat().st_size for p in child.rglob("*") if p.is_file()) if child.is_dir() else child.stat().st_size
                    items.append({"kind": "local", "path": str(child), "bytes": size})
            except FileNotFoundError:
                continue
    return {"dry_run": True, "older_than_days": older_than_days, "items": items,
            "item_count": len(items), "bytes": sum(item["bytes"] for item in items)}


def apply_retention(plan: dict[str, Any], *, confirm: str) -> dict[str, Any]:
    if confirm != "DELETE":
        raise ValueError("Refusing cleanup: pass the exact confirmation DELETE")
    removed = 0
    reclaimed = 0
    for item in plan.get("items", []):
        path = Path(item["path"])
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        removed += 1
        reclaimed += int(item.get("bytes") or 0)
    return {"dry_run": False, "removed": removed, "reclaimed_bytes": reclaimed}


def prune_job_history(
    *,
    older_than_days: int = 30,
    apply: bool = False,
    database: Database,
) -> dict[str, Any]:
    from mkb.db.models import BackgroundJob
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, older_than_days))
    terminal = ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED")
    with database.session() as session:
        ids = list(session.scalars(select(BackgroundJob.job_id).where(
            BackgroundJob.status.in_(terminal), BackgroundJob.updated_at < cutoff
        )))
        if apply and ids:
            session.execute(delete(BackgroundJob).where(BackgroundJob.job_id.in_(ids)))
            session.commit()
    return {"dry_run": not apply, "job_count": len(ids), "older_than_days": older_than_days}


def consistency_report(
    *,
    database: Database,
    object_store: ObjectStore,
) -> dict[str, Any]:
    """Compare database object references with object storage; never mutates either."""
    from mkb.config import settings
    from mkb.db.models import Asset, ProcessedAsset
    with database.session() as session:
        references = {(row.s3_bucket, row.s3_key) for model in (Asset, ProcessedAsset)
                      for row in session.scalars(select(model))}
    buckets = (settings.s3_bucket_raw, settings.s3_bucket_processed,
               settings.s3_bucket_archive, settings.s3_bucket_temp)
    objects: set[tuple[str, str]] = set()
    unavailable: list[str] = []
    for bucket in buckets:
        try:
            objects.update((bucket, item.key) for item in object_store.list(bucket))
        except Exception:  # dependency category is reported without leaking credentials
            unavailable.append(bucket)
    missing = sorted(references - objects)
    # temp/archive may legitimately contain objects not represented by asset rows.
    managed = {item for item in objects if item[0] in {settings.s3_bucket_raw, settings.s3_bucket_processed}}
    orphaned = sorted(managed - references)
    return {
        "ok": not missing and not unavailable,
        "database_references": len(references), "storage_objects": len(objects),
        "missing_objects": [{"bucket": b, "key": k} for b, k in missing],
        "orphaned_objects": [{"bucket": b, "key": k} for b, k in orphaned],
        "unavailable_buckets": unavailable,
    }
