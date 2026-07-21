"""Typed adapter over the existing durable web job manager."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from mkb.exceptions import ConflictError, NotFoundError
from mkb.models import Job
from mkb.ports import Capabilities


def _datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class WebJobBackend:
    """Expose JobManager state through the public JobBackend contract."""

    capabilities = frozenset({Capabilities.DURABLE_SUBMISSION})
    terminal_statuses = frozenset({"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"})

    def __init__(self, manager):
        self._manager = manager

    @staticmethod
    def _model(row: dict[str, Any]) -> Job:
        return Job(
            id=uuid.UUID(str(row["job_id"])),
            kind=str(row.get("kind") or "web"),
            status=str(row.get("status") or "QUEUED"),
            label=row.get("label"),
            project_id=row.get("project_id"),
            request_id=row.get("request_id"),
            active_key=row.get("active_key"),
            idempotency_key=row.get("idempotency_key"),
            events=tuple(row.get("events") or ()),
            attempt_count=int(row.get("attempt_count") or 0),
            max_attempts=int(row.get("max_attempts") or 1),
            retryable=bool(row.get("retryable")),
            cancel_requested=bool(row.get("cancel_requested")),
            message=row.get("current_message"),
            result=row.get("result"),
            error=row.get("error"),
            error_category=row.get("error_category"),
            created_at=_datetime(row.get("created_at")) or datetime.now().astimezone(),
            queued_at=_datetime(row.get("queued_at")),
            started_at=_datetime(row.get("started_at")),
            completed_at=_datetime(row.get("finished_at")),
            updated_at=_datetime(row.get("updated_at")),
        )

    def create(self, job: Job) -> Job:
        raise ConflictError(
            "Web jobs require a registered application action; use a pipeline or web action"
        )

    def update(self, job_id: str, **changes: Any) -> Job:
        raise ConflictError("Web job updates are owned by the running action manager")

    def get(self, job_id: str) -> Job | None:
        row = self._manager.get_job(str(job_id))
        return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Job]:
        rows = self._manager.list_jobs(limit=min(1000, limit + offset))
        return [self._model(row) for row in rows[offset : offset + limit]]

    def list_for_project(
        self,
        *,
        project_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Job]:
        rows = self._manager.list_jobs(
            limit=min(1000, limit + offset),
            project_id=project_id,
        )
        return [self._model(row) for row in rows[offset : offset + limit]]

    def wait(self, job_id: str, *, timeout: float | None = None) -> Job:
        started = time.monotonic()
        while True:
            job = self.get(job_id)
            if job is None:
                raise NotFoundError(f"Job not found: {job_id}")
            if job.status in self.terminal_statuses:
                return job
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError(f"Timed out waiting for job {job_id}")
            time.sleep(0.05)

    def cancel(self, job_id: str) -> Job:
        current = self.get(job_id)
        if current is None:
            raise NotFoundError(f"Job not found: {job_id}")
        if current.status not in {"QUEUED", "RUNNING", "CANCELLING"}:
            return current
        self._manager.cancel_job(str(job_id))
        return self.get(job_id)

    def cancel_all(self, *, project_id: str | None = None) -> list[Job]:
        identifiers = self._manager.cancel_all_active(project_id=project_id)
        return [job for identifier in identifiers if (job := self.get(identifier))]

    def find_active(
        self,
        *,
        project_id: str | None = None,
        kind: str | None = None,
    ) -> Job | None:
        row = self._manager.find_active_job(project_id=project_id, kind=kind)
        return self._model(row) if row else None

    def recover_interrupted(self) -> int:
        return self._manager.recover_interrupted()

    def close(self) -> None:
        return None


def serialize_web_job(job: Job) -> dict[str, Any]:
    """Preserve the established React-facing background-job payload."""

    def iso(value):
        return value.isoformat() if value is not None else None

    return {
        "job_id": str(job.id),
        "kind": job.kind,
        "label": job.label,
        "status": job.status,
        "project_id": job.project_id,
        "request_id": job.request_id,
        "idempotency_key": job.idempotency_key,
        "active_key": job.active_key,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "retryable": job.retryable,
        "cancel_requested": job.cancel_requested,
        "result": job.result,
        "error": job.error,
        "error_category": job.error_category,
        "current_message": job.message,
        "events": list(job.events),
        "created_at": iso(job.created_at),
        "queued_at": iso(job.queued_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.completed_at),
        "updated_at": iso(job.updated_at),
    }
