"""Typed grouped service for persisted pipeline jobs."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any

from mkb.exceptions import ConflictError, NotFoundError, ValidationError
from mkb.models import Job
from mkb.ports import JobBackend


def _job_id(value: str | uuid.UUID) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("job_id must be a UUID") from exc


class Jobs:
    """Query, wait for, and cooperatively cancel durable jobs."""

    def __init__(
        self,
        backend: JobBackend | None,
        action_submitter: Callable[..., str] | None = None,
    ):
        self._backend = backend
        self._action_submitter = action_submitter

    def _require_backend(self) -> JobBackend:
        if self._backend is None:
            raise ConflictError("Durable jobs are unavailable for this client")
        return self._backend

    def available(self) -> bool:
        """Return whether this client has a configured durable-job backend."""
        return self._backend is not None

    def get(self, job_id: str | uuid.UUID) -> Job | None:
        return self._require_backend().get(_job_id(job_id))

    def submit(
        self,
        *,
        kind: str,
        inputs: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        label: str | None = None,
        idempotency_key: str | None = None,
        job_id: str | uuid.UUID | None = None,
    ) -> Job:
        """Persist a queued application job for an external or custom worker."""
        if not kind.strip():
            raise ValidationError("job kind must not be empty")
        identifier = _job_id(job_id) if job_id is not None else str(uuid.uuid4())
        return self._require_backend().create(
            Job(
                id=uuid.UUID(identifier),
                kind=kind.strip(),
                status="QUEUED",
                label=label,
                idempotency_key=idempotency_key,
                inputs=inputs or {},
                parameters=parameters or {},
                created_at=datetime.now(timezone.utc),
            )
        )

    def submit_action(self, action: str, **kwargs: Any) -> Job:
        """Start an application-owned action through the configured job adapter."""
        if not action.strip():
            raise ValidationError("job action must not be empty")
        if self._action_submitter is None:
            raise ConflictError("Application job actions are unavailable for this client")
        return self.require(self._action_submitter(action.strip(), **kwargs))

    def require(self, job_id: str | uuid.UUID) -> Job:
        job = self.get(job_id)
        if job is None:
            raise NotFoundError(f"Job not found: {job_id}")
        return job

    def list(
        self,
        *,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Job]:
        if limit < 1 or limit > 1000:
            raise ValidationError("limit must be between 1 and 1000")
        if offset < 0:
            raise ValidationError("offset must be non-negative")
        backend = self._require_backend()
        if project_id is not None:
            operation = getattr(backend, "list_for_project", None)
            if callable(operation):
                return operation(project_id=project_id, limit=limit, offset=offset)
            return [
                job
                for job in backend.list(limit=1000, offset=0)
                if job.project_id == project_id
            ][offset : offset + limit]
        return backend.list(limit=limit, offset=offset)

    def wait(
        self,
        job_id: str | uuid.UUID,
        *,
        timeout: float | None = None,
    ) -> Job:
        if timeout is not None and timeout < 0:
            raise ValidationError("timeout must be non-negative")
        return self._require_backend().wait(_job_id(job_id), timeout=timeout)

    def cancel(self, job_id: str | uuid.UUID) -> Job:
        return self._require_backend().cancel(_job_id(job_id))

    def cancel_all(self, *, project_id: str | None = None) -> list[Job]:
        backend = self._require_backend()
        operation = getattr(backend, "cancel_all", None)
        if callable(operation):
            return operation(project_id=project_id)
        cancelled = []
        for job in self.list(limit=1000):
            if project_id is not None and job.project_id != project_id:
                continue
            if job.status in {"QUEUED", "RUNNING", "CANCELLING"}:
                cancelled.append(backend.cancel(str(job.id)))
        return cancelled

    def find_active(
        self,
        *,
        project_id: str | None = None,
        kind: str | None = None,
    ) -> Job | None:
        backend = self._require_backend()
        operation = getattr(backend, "find_active", None)
        if callable(operation):
            return operation(project_id=project_id, kind=kind)
        for job in self.list(limit=1000):
            if job.status not in {"QUEUED", "RUNNING", "CANCELLING"}:
                continue
            if project_id is not None and job.project_id != project_id:
                continue
            if kind is not None and job.kind != kind:
                continue
            return job
        return None

    def events(
        self,
        job_id: str | uuid.UUID,
        *,
        after: int = 0,
        follow: bool = False,
        timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> Iterator[dict[str, Any]]:
        """Yield persisted events, optionally following until the job is terminal."""
        if after < 0:
            raise ValidationError("after must be non-negative")
        if timeout is not None and timeout < 0:
            raise ValidationError("timeout must be non-negative")
        if poll_interval <= 0:
            raise ValidationError("poll_interval must be positive")
        identifier = _job_id(job_id)
        cursor = after
        started = time.monotonic()
        terminal = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}
        while True:
            job = self._require_backend().get(identifier)
            if job is None:
                raise NotFoundError(f"Job not found: {job_id}")
            while cursor < len(job.events):
                yield dict(job.events[cursor])
                cursor += 1
            if not follow or job.status in terminal:
                return
            if timeout is not None and time.monotonic() - started >= timeout:
                return
            time.sleep(poll_interval)

    def recover_interrupted(self) -> int:
        """Explicitly release jobs abandoned by a stopped worker process."""
        return self._require_backend().recover_interrupted()
