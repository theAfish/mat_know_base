"""Read-only inventory used to prove that SDK migrations preserve local data."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, select, text


DEFAULT_LOCAL_PATHS = (
    Path("data/papers"),
    Path("data/processed"),
    Path("data/uploads"),
    Path("data/inbox"),
    Path("data/runtime_settings.json"),
)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_local_files(paths: Iterable[Path]) -> Iterable[tuple[Path, Path]]:
    """Yield ``(configured root, file)`` pairs in deterministic order."""
    for configured in paths:
        path = Path(configured)
        if path.is_file():
            yield path, path
        elif path.is_dir():
            for child in sorted(item for item in path.rglob("*") if item.is_file()):
                yield path, child


def local_inventory(paths: Iterable[Path] = DEFAULT_LOCAL_PATHS) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for root, path in _iter_local_files(paths):
        relative = path.name if root.is_file() else path.relative_to(root).as_posix()
        stat = path.stat()
        files.append({
            "root": root.as_posix(),
            "path": relative,
            "bytes": stat.st_size,
            "sha256": _sha256_file(path),
        })
    return {
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "files": files,
    }


def database_inventory(session_factory=None) -> dict[str, Any]:
    """Count every mapped table and record its complete primary-key set."""
    if session_factory is None:
        from mkb.db.engine import SyncSessionLocal

        session_factory = SyncSessionLocal
    from mkb.db.models import Base

    tables: dict[str, Any] = {}
    with session_factory() as session:
        database_version = session.execute(text("select version()")).scalar_one()
        alembic_revision = session.execute(
            text("select version_num from alembic_version")
        ).scalar_one()
        for table in Base.metadata.sorted_tables:
            primary_key = list(table.primary_key.columns)
            count = session.execute(select(func.count()).select_from(table)).scalar_one()
            rows: list[Any] = []
            if primary_key:
                for row in session.execute(select(*primary_key).order_by(*primary_key)):
                    values = [_json_value(value) for value in row]
                    rows.append(values[0] if len(values) == 1 else values)
            tables[table.name] = {
                "row_count": int(count),
                "primary_key_columns": [column.name for column in primary_key],
                "primary_keys": rows,
            }
    return {
        "alembic_revision": alembic_revision,
        "database_version": database_version,
        "tables": tables,
    }


def object_storage_inventory(
    client=None,
    buckets: Iterable[str] | None = None,
    *,
    include_checksums: bool = False,
) -> dict[str, Any]:
    """List object identity, optionally streaming every object for SHA-256."""
    if client is None:
        from mkb.storage.s3 import get_s3_client

        client = get_s3_client()
    if buckets is None:
        from mkb.config import settings

        buckets = (
            settings.s3_bucket_raw,
            settings.s3_bucket_processed,
            settings.s3_bucket_archive,
            settings.s3_bucket_temp,
        )

    result: dict[str, Any] = {}
    for bucket in buckets:
        objects: list[dict[str, Any]] = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                record = {
                    "key": item["Key"],
                    "bytes": int(item.get("Size") or 0),
                    # ETag is an identity aid, but is not guaranteed to be an MD5 for
                    # multipart or implementation-specific uploads.
                    "etag": str(item.get("ETag") or "").strip('"'),
                    "last_modified": _json_value(item.get("LastModified")),
                }
                if include_checksums:
                    digest = hashlib.sha256()
                    body = client.get_object(Bucket=bucket, Key=item["Key"])["Body"]
                    try:
                        while chunk := body.read(1024 * 1024):
                            digest.update(chunk)
                    finally:
                        body.close()
                    record["sha256"] = digest.hexdigest()
                objects.append(record)
        objects.sort(key=lambda item: item["key"])
        result[str(bucket)] = {
            "object_count": len(objects),
            "total_bytes": sum(item["bytes"] for item in objects),
            "objects": objects,
        }
    return {"buckets": result}


def _git_commit(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def _package_version() -> str:
    try:
        return importlib.metadata.version("mat-know-base")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def compose_manifest(root: Path) -> dict[str, Any]:
    """Return non-secret Compose identity/version metadata when Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False}
    if result.returncode != 0:
        return {"available": False}
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    projects = sorted({str(row.get("Project")) for row in rows if row.get("Project")})
    services = {}
    for row in rows:
        name = str(row.get("Service") or "unknown")
        labels = {
            token.split("=", 1)[0]: token.split("=", 1)[1]
            for token in str(row.get("Labels") or "").split(",")
            if "=" in token
        }
        services[name] = {
            "image": row.get("Image"),
            "version": labels.get("version") or labels.get("release"),
            "state": row.get("State"),
            "health": row.get("Health"),
        }
    return {
        "available": True,
        "project": projects[0] if len(projects) == 1 else projects,
        "services": services,
    }


def safe_configuration() -> dict[str, Any]:
    """Return storage topology needed for restoration, excluding credentials."""
    from mkb.config import settings

    return {
        "deployment_mode": settings.deployment_mode.value,
        "database": {
            "host": settings.pg_host,
            "port": settings.pg_port,
            "database": settings.pg_database,
        },
        "object_storage": {
            "endpoint": settings.s3_endpoint,
            "buckets": {
                "raw": settings.s3_bucket_raw,
                "processed": settings.s3_bucket_processed,
                "archive": settings.s3_bucket_archive,
                "temp": settings.s3_bucket_temp,
            },
        },
        "processed_local_root": settings.processed_local_root,
        "runtime_settings_path": settings.runtime_settings_path,
    }


def migration_inventory(
    *,
    session_factory=None,
    s3_client=None,
    local_paths: Iterable[Path] = DEFAULT_LOCAL_PATHS,
    root: Path | None = None,
    include_object_checksums: bool = False,
) -> dict[str, Any]:
    """Build a deterministic, read-only preservation baseline for local migrations."""
    project_root = root or Path(__file__).resolve().parents[2]
    database = database_inventory(session_factory)
    storage = object_storage_inventory(
        s3_client,
        include_checksums=include_object_checksums,
    )
    return {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "application": {
            "package": "mat-know-base",
            "version": _package_version(),
            "git_commit": _git_commit(project_root),
        },
        "configuration": safe_configuration(),
        "infrastructure": compose_manifest(project_root),
        "database": database,
        "object_storage": storage,
        "local_files": local_inventory(local_paths),
    }
