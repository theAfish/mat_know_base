"""Small cross-domain grouped services owned by a configured client."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from mkb.models import EffectiveSettings, MaintenanceReport

if TYPE_CHECKING:
    from mkb.sdk import KnowledgeBase


class SettingsService:
    """Inspect effective non-secret configuration."""

    def __init__(self, knowledge_base: KnowledgeBase):
        self._knowledge_base = knowledge_base

    def inspect(self) -> EffectiveSettings:
        config = self._knowledge_base.config
        parsed = urlparse(config.database_url or "")
        return EffectiveSettings(
            database_backend=parsed.scheme or None,
            object_store_endpoint=config.object_store_endpoint,
            buckets={
                "raw": config.raw_bucket,
                "processed": config.processed_bucket,
                "archive": config.archive_bucket,
                "temp": config.temp_bucket,
            },
            capabilities=tuple(sorted(self._knowledge_base.pipelines.capabilities)),
        )


class MaintenanceService:
    """Read-only inventory, reconciliation, and cleanup planning."""

    def __init__(self, knowledge_base: KnowledgeBase):
        self._knowledge_base = knowledge_base

    def inventory(self) -> MaintenanceReport:
        kb = self._knowledge_base
        object_counts = {}
        if kb.object_store is not None:
            for bucket in (
                kb.config.raw_bucket,
                kb.config.processed_bucket,
                kb.config.archive_bucket,
                kb.config.temp_bucket,
            ):
                objects = list(kb.object_store.list(bucket))
                object_counts[bucket] = {
                    "object_count": len(objects),
                    "total_bytes": sum(item.size for item in objects),
                    "keys": [item.key for item in objects],
                }
        counts = {}
        for name in ("collections", "sources", "artifacts", "records", "schemas"):
            service = getattr(kb, name, None)
            if service is not None:
                counts[name] = len(service.list(limit=1000))
        return MaintenanceReport(
            kind="inventory",
            ok=True,
            data={
                "schema_version": kb.schema_version(),
                "resource_counts": counts,
                "object_storage": object_counts,
            },
            created_at=datetime.now(timezone.utc),
        )

    def reconcile(self) -> MaintenanceReport:
        kb = self._knowledge_base
        missing = []
        if kb.object_store is not None:
            for service_name in ("sources", "artifacts"):
                service = getattr(kb, service_name, None)
                if service is None:
                    continue
                for item in service.list(limit=1000):
                    storage = item.storage
                    if storage is not None and not kb.object_store.exists(
                        storage.bucket, storage.key
                    ):
                        missing.append(
                            {
                                "resource_type": service_name[:-1],
                                "resource_id": str(item.id),
                                "bucket": storage.bucket,
                                "key": storage.key,
                            }
                        )
        return MaintenanceReport(
            kind="reconcile",
            ok=not missing,
            data={"missing_objects": missing, "missing_count": len(missing)},
            created_at=datetime.now(timezone.utc),
        )

    def backup_metadata(self) -> MaintenanceReport:
        settings = SettingsService(self._knowledge_base).inspect()
        return MaintenanceReport(
            kind="backup_metadata",
            ok=True,
            data=settings.model_dump(mode="json"),
            created_at=datetime.now(timezone.utc),
        )

    def cleanup_plan(
        self,
        *,
        older_than_days: int = 7,
        roots: list[str | Path] | None = None,
    ) -> MaintenanceReport:
        from mkb.maintenance import retention_plan

        plan = retention_plan(
            older_than_days=older_than_days,
            roots=[Path(root) for root in roots] if roots is not None else None,
        )
        return MaintenanceReport(
            kind="cleanup_plan",
            ok=True,
            data=plan,
            created_at=datetime.now(timezone.utc),
        )
