"""Non-sensitive process and dependency diagnostics."""

from __future__ import annotations

from typing import Any, Callable

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from mkb.config import settings


def check_database() -> dict[str, Any]:
    from mkb.db.engine import sync_engine

    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    with sync_engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        current = MigrationContext.configure(connection).get_current_revision()
    if current != expected:
        return {
            "ok": False,
            "category": "schema_revision",
            "message": "Database migration required",
            "current_revision": current,
            "expected_revision": expected,
        }
    return {"ok": True, "revision": current}


def check_object_storage() -> dict[str, Any]:
    from mkb.storage.s3 import get_s3_client

    buckets = (
        settings.s3_bucket_raw,
        settings.s3_bucket_processed,
        settings.s3_bucket_archive,
        settings.s3_bucket_temp,
    )
    client = get_s3_client()
    for bucket in buckets:
        client.head_bucket(Bucket=bucket)
    return {"ok": True, "bucket_count": len(buckets)}


def check_worker() -> dict[str, Any]:
    from mkb.web._state import jobs

    return {"ok": jobs is not None and settings.max_concurrent_jobs > 0}


def readiness_report(
    checks: dict[str, Callable[[], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    selected = checks or {
        "database": check_database,
        "object_storage": check_object_storage,
        "worker": check_worker,
    }
    components: dict[str, dict[str, Any]] = {}
    for name, check in selected.items():
        try:
            components[name] = check()
        except Exception as exc:
            components[name] = {
                "ok": False,
                "category": "dependency_unavailable",
                "message": type(exc).__name__,
            }
    ready = all(component.get("ok") is True for component in components.values())
    return {"status": "ready" if ready else "not_ready", "components": components}
