"""Small cross-domain grouped services owned by a configured client."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from mkb.exceptions import ConflictError, ValidationError
from mkb.models import EffectiveSettings, MaintenanceReport

if TYPE_CHECKING:
    from mkb.models import Job
    from mkb.sdk import KnowledgeBase


class AssistantService:
    """Own one lazy assistant session and submit its work as durable jobs."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        *,
        session_factory: Callable[[], tuple[Any, str]] | None = None,
        pending_workflows: Callable[[], list[dict[str, Any]]] | None = None,
        action_resolver: Callable[[str], str] | None = None,
    ):
        self._knowledge_base = knowledge_base
        self._session_factory = session_factory
        self._pending_workflows = pending_workflows
        self._action_resolver = action_resolver
        self._session: tuple[Any, str] | None = None
        self._lock = threading.Lock()

    def _get_session(self) -> tuple[Any, str]:
        if self._session_factory is None:
            raise ConflictError("Assistant sessions are unavailable for this client")
        with self._lock:
            if self._session is None:
                self._session = self._session_factory()
            return self._session

    def _dispatch_pending_workflows(self) -> None:
        if self._pending_workflows is None or self._action_resolver is None:
            return
        for request in self._pending_workflows():
            kind = str(request.get("kind") or "workflow")
            action = request.get("action") or self._action_resolver(kind)
            self._knowledge_base.jobs.submit_action(
                str(action),
                job_project_id=request.get("project_id"),
                label=request.get("label") or kind,
                **dict(request.get("kwargs") or {}),
            )

    def chat(self, message: str) -> Job:
        """Submit a message to this client's persistent assistant session."""
        clean_message = message.strip()
        if not clean_message:
            raise ValidationError("assistant message must not be empty")
        runner, session_id = self._get_session()
        return self._knowledge_base.jobs.submit_action(
            "assistant_chat",
            runner=runner,
            session_id=session_id,
            message=clean_message,
            dispatch_pending_workflows=self._dispatch_pending_workflows,
        )


class SettingsService:
    """Inspect effective non-secret configuration."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        *,
        runtime_reader: Callable[[], dict[str, Any]] | None = None,
        runtime_updater: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        startup_validator: Callable[..., list[str]] | None = None,
    ):
        self._knowledge_base = knowledge_base
        self._runtime_reader = runtime_reader
        self._runtime_updater = runtime_updater
        self._startup_validator = startup_validator

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

    def runtime(self) -> dict[str, Any]:
        """Return mutable application settings with secrets already masked."""
        if self._runtime_reader is None:
            return self.inspect().model_dump(mode="json")
        return self._runtime_reader()

    def update(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist supported runtime-setting overrides."""
        if self._runtime_updater is None:
            raise ConflictError(
                "Runtime setting updates are unavailable for this client"
            )
        return self._runtime_updater(dict(updates))

    def validate_startup(
        self,
        *,
        host: str | None = None,
        log_level: str | None = None,
    ) -> list[str]:
        """Return deployment-safety warnings from the configured application."""
        if self._startup_validator is None:
            return []
        return self._startup_validator(host=host, log_level=log_level)


class MaintenanceService:
    """Read-only inventory, reconciliation, and cleanup planning."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        *,
        migration_inventory_reader: Callable[[], dict[str, Any]] | None = None,
        cleanup_executor: Callable[..., dict[str, Any]] | None = None,
    ):
        self._knowledge_base = knowledge_base
        self._migration_inventory_reader = migration_inventory_reader
        self._cleanup_executor = cleanup_executor

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
        references: set[tuple[str, str]] = set()
        objects: set[tuple[str, str]] = set()
        if kb.object_store is not None:
            for service_name in ("sources", "artifacts"):
                service = getattr(kb, service_name, None)
                if service is None:
                    continue
                for item in service.list(limit=1000):
                    storage = item.storage
                    if storage is None:
                        continue
                    references.add((storage.bucket, storage.key))
                    if not kb.object_store.exists(storage.bucket, storage.key):
                        missing.append(
                            {
                                "resource_type": service_name[:-1],
                                "resource_id": str(item.id),
                                "bucket": storage.bucket,
                                "key": storage.key,
                            }
                        )
            for bucket in (kb.config.raw_bucket, kb.config.processed_bucket):
                objects.update(
                    (item.bucket, item.key) for item in kb.object_store.list(bucket)
                )
        orphaned = sorted(objects - references)
        return MaintenanceReport(
            kind="reconcile",
            ok=not missing,
            data={
                "database_references": len(references),
                "storage_objects": len(objects),
                "missing_objects": missing,
                "missing_count": len(missing),
                "orphaned_objects": [
                    {"bucket": bucket, "key": key} for bucket, key in orphaned
                ],
                "orphaned_count": len(orphaned),
            },
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

    def migration_inventory(self) -> MaintenanceReport:
        """Return the application-wide preservation inventory when configured."""
        if self._migration_inventory_reader is None:
            return self.inventory()
        return MaintenanceReport(
            kind="migration_inventory",
            ok=True,
            data=self._migration_inventory_reader(),
            created_at=datetime.now(timezone.utc),
        )

    def cleanup(
        self,
        *,
        older_than_days: int = 7,
        job_days: int = 30,
        apply: bool = False,
        confirm: str | None = None,
    ) -> MaintenanceReport:
        """Plan or apply retention through an explicitly bound application adapter."""
        if apply and confirm != "DELETE":
            raise ValidationError("cleanup apply requires confirm='DELETE'")
        if self._cleanup_executor is None:
            if apply:
                raise ConflictError("Cleanup application is unavailable for this client")
            plan = self.cleanup_plan(older_than_days=older_than_days)
            return plan.model_copy(update={"kind": "cleanup"})
        data = self._cleanup_executor(
            older_than_days=older_than_days,
            job_days=job_days,
            apply=apply,
            confirm=confirm,
        )
        return MaintenanceReport(
            kind="cleanup",
            ok=True,
            data=data,
            created_at=datetime.now(timezone.utc),
        )
