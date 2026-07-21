"""Non-sensitive process and dependency diagnostics."""

from __future__ import annotations

from typing import Any, Callable

from mkb.web.dependencies import get_knowledge_base


def check_database() -> dict[str, Any]:
    kb = get_knowledge_base()
    kb.database.check()
    revision_reader = getattr(kb.database, "migration_revision", None)
    revision = revision_reader() if callable(revision_reader) else kb.schema_version()
    return {"ok": True, "revision": revision}


def check_object_storage() -> dict[str, Any]:
    kb = get_knowledge_base()
    buckets = (
        kb.config.raw_bucket,
        kb.config.processed_bucket,
        kb.config.archive_bucket,
        kb.config.temp_bucket,
    )
    kb.object_store.check(buckets)
    return {"ok": True, "bucket_count": len(buckets)}


def check_worker() -> dict[str, Any]:
    return {"ok": get_knowledge_base().jobs.available()}


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
