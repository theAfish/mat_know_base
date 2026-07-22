"""Typed read/control adapter for the existing background_jobs table."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from mkb.exceptions import ConflictError, NotFoundError
from mkb.models import Job


class SQLAlchemyLegacyJobBackend:
    """Expose persisted application jobs outside the FastAPI process.

    Creation remains owned by the application action manager because legacy rows do
    not contain arbitrary pipeline inputs. Portable clients use GenericJobBackend for
    full custom pipeline submission.
    """

    capabilities = frozenset()
    terminal_statuses = frozenset({"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"})

    def __init__(self, database):
        self._database = database

    @staticmethod
    def _model(row) -> Job:
        return Job(
            id=row.job_id,
            kind=row.kind,
            status=row.status,
            label=row.label,
            project_id=row.project_id,
            request_id=row.request_id,
            active_key=row.active_key,
            idempotency_key=row.idempotency_key,
            events=tuple(row.events or ()),
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            retryable=row.retryable,
            cancel_requested=row.cancel_requested,
            message=row.current_message,
            result=row.result,
            error=row.error,
            error_category=row.error_category,
            created_at=row.created_at,
            queued_at=row.queued_at,
            started_at=row.started_at,
            completed_at=row.finished_at,
            updated_at=row.updated_at,
        )

    def create(self, job: Job) -> Job:
        raise ConflictError(
            "Legacy application jobs require a registered application action; "
            "use a portable client for custom durable pipeline submission"
        )

    def update(self, job_id: str, **changes) -> Job:
        from mkb.db.models import BackgroundJob

        with self._database.transaction() as session:
            row = session.get(BackgroundJob, uuid.UUID(str(job_id)))
            if row is None:
                raise NotFoundError(f"Job not found: {job_id}")
            aliases = {
                "message": "current_message",
                "completed_at": "finished_at",
            }
            for name, value in changes.items():
                target = aliases.get(name, name)
                if hasattr(row, target):
                    setattr(row, target, value)
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self._model(row)

    def get(self, job_id: str) -> Job | None:
        from mkb.db.models import BackgroundJob

        with self._database.session() as session:
            row = session.get(BackgroundJob, uuid.UUID(str(job_id)))
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Job]:
        from mkb.db.models import BackgroundJob

        statement = (
            select(BackgroundJob)
            .order_by(BackgroundJob.updated_at.desc(), BackgroundJob.job_id)
            .limit(limit)
            .offset(offset)
        )
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]

    def list_for_project(
        self, *, project_id: str, limit: int = 100, offset: int = 0
    ) -> list[Job]:
        from mkb.db.models import BackgroundJob

        statement = (
            select(BackgroundJob)
            .where(BackgroundJob.project_id == project_id)
            .order_by(BackgroundJob.updated_at.desc(), BackgroundJob.job_id)
            .limit(limit)
            .offset(offset)
        )
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]

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
        job = self.get(job_id)
        if job is None:
            raise NotFoundError(f"Job not found: {job_id}")
        if job.status in self.terminal_statuses:
            return job
        status = "CANCELLED" if job.status == "QUEUED" else "CANCELLING"
        return self.update(
            job_id,
            status=status,
            cancel_requested=True,
            active_key=None if status == "CANCELLED" else job.active_key,
            message="Cancellation requested",
            completed_at=datetime.now(timezone.utc) if status == "CANCELLED" else None,
        )

    def find_active(
        self, *, project_id: str | None = None, kind: str | None = None
    ) -> Job | None:
        from mkb.db.models import BackgroundJob

        statement = select(BackgroundJob).where(
            BackgroundJob.status.in_({"QUEUED", "RUNNING", "CANCELLING"})
        )
        if project_id is not None:
            statement = statement.where(BackgroundJob.project_id == project_id)
        if kind is not None:
            statement = statement.where(BackgroundJob.kind == kind)
        with self._database.session() as session:
            row = session.scalar(statement.order_by(BackgroundJob.created_at).limit(1))
            return self._model(row) if row else None

    def recover_interrupted(self) -> int:
        from mkb.db.models import BackgroundJob

        with self._database.session() as session:
            identifiers = list(
                session.scalars(
                    select(BackgroundJob.job_id).where(
                        BackgroundJob.status.in_({"QUEUED", "RUNNING", "CANCELLING"})
                    )
                )
            )
        for identifier in identifiers:
            self.update(
                str(identifier),
                status="INTERRUPTED",
                active_key=None,
                error="Application stopped before the job completed",
                error_category="worker_interrupted",
                message="Interrupted by restart",
                completed_at=datetime.now(timezone.utc),
            )
        return len(identifiers)

    def close(self) -> None:
        return None
