"""Small cross-domain grouped services owned by a configured client."""

from __future__ import annotations

import hashlib
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
        namespaces: set[tuple[str, str]] = set()
        artifact_source_ids: set[str] = set()
        objects: set[tuple[str, str]] = set()

        def all_items(service):
            offset = 0
            while True:
                page = service.list(limit=1000, offset=offset)
                yield from page
                if len(page) < 1000:
                    return
                offset += len(page)

        if kb.object_store is not None:
            for service_name in ("sources", "artifacts"):
                service = getattr(kb, service_name, None)
                if service is None:
                    continue
                for item in all_items(service):
                    if service_name == "artifacts":
                        artifact_source_ids.add(str(item.source_id))
                    storage = item.storage
                    if storage is None:
                        continue
                    references.add((storage.bucket, storage.key))
                    if "/" in storage.key:
                        namespaces.add(
                            (storage.bucket, storage.key.rsplit("/", 1)[0] + "/")
                        )
                    if not kb.object_store.exists(storage.bucket, storage.key):
                        missing.append(
                            {
                                "resource_type": service_name[:-1],
                                "resource_id": str(item.id),
                                "bucket": storage.bucket,
                                "key": storage.key,
                            }
                        )
            for bucket in (
                kb.config.raw_bucket,
                kb.config.processed_bucket,
                kb.config.archive_bucket,
                kb.config.temp_bucket,
            ):
                objects.update(
                    (item.bucket, item.key) for item in kb.object_store.list(bucket)
                )
        unreferenced = objects - references
        related = sorted(
            (bucket, key)
            for bucket, key in unreferenced
            if (
                any(
                    bucket == ref_bucket and key.startswith(prefix)
                    for ref_bucket, prefix in namespaces
                )
                or (
                    bucket == kb.config.processed_bucket
                    and len(key.split("/")) > 1
                    and key.split("/")[1] in artifact_source_ids
                )
            )
        )
        orphaned = sorted(unreferenced - set(related))
        return MaintenanceReport(
            kind="reconcile",
            ok=not missing,
            data={
                "database_references": len(references),
                "storage_objects": len(objects),
                "missing_objects": missing,
                "missing_count": len(missing),
                "related_objects": [
                    {"bucket": bucket, "key": key} for bucket, key in related
                ],
                "related_count": len(related),
                "orphaned_objects": [
                    {"bucket": bucket, "key": key} for bucket, key in orphaned
                ],
                "orphaned_count": len(orphaned),
            },
            created_at=datetime.now(timezone.utc),
        )

    def verify_content(self, *, sample_size: int = 10) -> MaintenanceReport:
        """Verify every storage reference and checksum deterministic content samples."""
        if sample_size < 1 or sample_size > 100:
            raise ValidationError("sample_size must be between 1 and 100")
        kb = self._knowledge_base
        if kb.object_store is None:
            raise ConflictError("Object storage is unavailable for this client")

        def all_items(service):
            items = []
            offset = 0
            while True:
                page = service.list(limit=1000, offset=offset)
                items.extend(page)
                if len(page) < 1000:
                    return sorted(items, key=lambda item: str(item.id))
                offset += len(page)

        def sample(items):
            if len(items) <= sample_size:
                return items
            if sample_size == 1:
                return [items[0]]
            return [
                items[index * (len(items) - 1) // (sample_size - 1)]
                for index in range(sample_size)
            ]

        missing = []
        mismatches = []
        sampled = []
        for resource_type in ("source", "artifact"):
            service = getattr(kb, resource_type + "s", None)
            if service is None:
                continue
            items = [item for item in all_items(service) if item.storage is not None]
            for item in items:
                storage = item.storage
                if not kb.object_store.exists(storage.bucket, storage.key):
                    missing.append({
                        "resource_type": resource_type,
                        "resource_id": str(item.id),
                        "bucket": storage.bucket,
                        "key": storage.key,
                    })
            missing_ids = {item["resource_id"] for item in missing}
            for item in sample(items):
                if str(item.id) in missing_ids:
                    continue
                storage = item.storage
                content_checksum = hashlib.sha256()
                verified_checksum = hashlib.sha256()
                size = 0
                with kb.object_store.open(storage.bucket, storage.key) as stream:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        content_checksum.update(chunk)
                        verified_checksum.update(chunk)
                content_sha256 = content_checksum.hexdigest()
                artifact_files = (
                    sorted(item.metadata.get("artifact_files") or [])
                    if resource_type == "artifact"
                    else []
                )
                bundle_complete = True
                prefix = storage.key.rsplit("/", 1)[0] if "/" in storage.key else ""
                for relative_path in artifact_files:
                    artifact_key = f"{prefix}/{relative_path}" if prefix else relative_path
                    if not kb.object_store.exists(storage.bucket, artifact_key):
                        missing.append({
                            "resource_type": "artifact_file",
                            "resource_id": str(item.id),
                            "bucket": storage.bucket,
                            "key": artifact_key,
                        })
                        bundle_complete = False
                        continue
                    artifact_checksum = hashlib.sha256()
                    with kb.object_store.open(storage.bucket, artifact_key) as stream:
                        while chunk := stream.read(1024 * 1024):
                            artifact_checksum.update(chunk)
                    verified_checksum.update(relative_path.encode("utf-8"))
                    verified_checksum.update(artifact_checksum.digest())
                actual_sha256 = verified_checksum.hexdigest()
                expected_sha256 = str(item.sha256)
                if (
                    size != int(item.size)
                    or (bundle_complete and actual_sha256 != expected_sha256)
                ):
                    mismatches.append({
                        "resource_type": resource_type,
                        "resource_id": str(item.id),
                        "expected_bytes": int(item.size),
                        "actual_bytes": size,
                        "expected_sha256": expected_sha256,
                        "actual_sha256": actual_sha256,
                        "checksum_scope": "bundle" if artifact_files else "content",
                    })
                sampled.append({
                    "resource_type": resource_type,
                    "resource_id": str(item.id),
                    "bytes": size,
                    "content_sha256": content_sha256,
                    "verified_sha256": actual_sha256,
                    "checksum_scope": "bundle" if artifact_files else "content",
                })

        return MaintenanceReport(
            kind="content_verification",
            ok=not missing and not mismatches,
            data={
                "sample_size_per_resource": sample_size,
                "sampled": sampled,
                "sampled_count": len(sampled),
                "missing_objects": missing,
                "missing_count": len(missing),
                "mismatches": mismatches,
                "mismatch_count": len(mismatches),
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

    def migration_inventory(
        self,
        *,
        include_object_checksums: bool = False,
    ) -> MaintenanceReport:
        """Return the application-wide preservation inventory when configured."""
        if self._migration_inventory_reader is None:
            return self.inventory()
        options = (
            {"include_object_checksums": True}
            if include_object_checksums
            else {}
        )
        return MaintenanceReport(
            kind="migration_inventory",
            ok=True,
            data=self._migration_inventory_reader(**options),
            created_at=datetime.now(timezone.utc),
        )

    def compare_inventories(
        self,
        before: dict[str, Any] | str | Path,
        after: dict[str, Any] | str | Path,
    ) -> MaintenanceReport:
        """Compare two preservation inventories without reading live backends."""
        from mkb.migration_preflight import (
            compare_migration_inventories,
            load_inventory,
        )

        old = before if isinstance(before, dict) else load_inventory(before)
        new = after if isinstance(after, dict) else load_inventory(after)
        data = compare_migration_inventories(old, new)
        return MaintenanceReport(
            kind="migration_preflight",
            ok=bool(data["ok"]),
            data=data,
            created_at=datetime.now(timezone.utc),
        )

    def restore_missing_artifact(
        self,
        artifact_id: str,
        local_file: str | Path,
        *,
        apply: bool = False,
        confirm: str | None = None,
    ) -> MaintenanceReport:
        """Checksum-gate restoration of one missing artifact object.

        The default is a read-only dry run. Applying is additive and idempotent,
        and is permitted only when the local file exactly matches the immutable
        size and SHA-256 stored with the artifact.
        """
        kb = self._knowledge_base
        if kb.artifacts is None or kb.object_store is None:
            raise ConflictError("Artifact storage is unavailable for this client")
        if apply and confirm != "RESTORE MISSING OBJECT":
            raise ValidationError(
                "restore apply requires confirm='RESTORE MISSING OBJECT'"
            )
        artifact = kb.artifacts.require(artifact_id)
        if artifact.storage is None:
            raise ConflictError(f"Artifact {artifact.id} has no object-store reference")
        path = Path(local_file)
        if not path.is_file():
            raise ValidationError(f"Local recovery file does not exist: {path}")

        def digest(stream) -> str:
            checksum = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                checksum.update(chunk)
            return checksum.hexdigest()

        local_size = path.stat().st_size
        with path.open("rb") as stream:
            local_sha256 = digest(stream)
        expected_size = int(artifact.size)
        expected_sha256 = str(artifact.sha256)
        if local_size != expected_size or local_sha256 != expected_sha256:
            raise ConflictError(
                "Local recovery file does not match artifact metadata: "
                f"size={local_size}/{expected_size}, "
                f"sha256={local_sha256}/{expected_sha256}"
            )

        bucket = artifact.storage.bucket
        key = artifact.storage.key
        if kb.object_store.exists(bucket, key):
            with kb.object_store.open(bucket, key) as stream:
                stored_sha256 = digest(stream)
            if stored_sha256 != expected_sha256:
                raise ConflictError(
                    f"Refusing to overwrite mismatched existing object {bucket}/{key}"
                )
            status = "already_present"
            applied = False
        elif apply:
            kb.object_store.put_bytes(bucket, key, path.read_bytes())
            with kb.object_store.open(bucket, key) as stream:
                stored_sha256 = digest(stream)
            if stored_sha256 != expected_sha256:
                raise ConflictError(
                    f"Restored object failed verification for {bucket}/{key}"
                )
            status = "restored"
            applied = True
        else:
            status = "ready"
            applied = False

        return MaintenanceReport(
            kind="migration_repair",
            ok=True,
            data={
                "status": status,
                "dry_run": not apply,
                "applied": applied,
                "artifact_id": str(artifact.id),
                "bucket": bucket,
                "key": key,
                "local_file": str(path.resolve()),
                "bytes": expected_size,
                "sha256": expected_sha256,
            },
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
