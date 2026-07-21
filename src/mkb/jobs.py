"""Persistence boundary for background jobs."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError


ACTIVE_STATUSES = {"QUEUED", "RUNNING", "CANCELLING"}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


class JobConflict(RuntimeError):
    """An idempotency or active-work constraint rejected a new job."""

    def __init__(self, existing: dict[str, Any]) -> None:
        self.existing = existing
        super().__init__(f"Job {existing['job_id']} already represents this work")


class JobStore(Protocol):
    def create(self, row: dict[str, Any]) -> dict[str, Any]: ...
    def update(self, job_id: str, **changes: Any) -> dict[str, Any] | None: ...
    def get(self, job_id: str) -> dict[str, Any] | None: ...
    def list(self, *, limit: int, project_id: str | None = None) -> list[dict[str, Any]]: ...
    def find_active(self, *, project_id: str | None = None, kind: str | None = None) -> dict[str, Any] | None: ...
    def recover_interrupted(self) -> int: ...


class MemoryJobStore:
    """Test/local adapter with the same semantics as the PostgreSQL store."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def create(self, row: dict[str, Any]) -> dict[str, Any]:
        for existing in self.rows.values():
            if row.get("idempotency_key") and existing.get("idempotency_key") == row["idempotency_key"]:
                raise JobConflict(copy.deepcopy(existing))
            if row.get("active_key") and existing.get("active_key") == row["active_key"]:
                raise JobConflict(copy.deepcopy(existing))
        self.rows[row["job_id"]] = copy.deepcopy(row)
        return copy.deepcopy(row)

    def update(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        row = self.rows.get(job_id)
        if row is None:
            return None
        row.update(copy.deepcopy(changes))
        return copy.deepcopy(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.rows.get(job_id)
        return copy.deepcopy(row) if row else None

    def list(self, *, limit: int, project_id: str | None = None) -> list[dict[str, Any]]:
        rows = list(self.rows.values())
        if project_id is not None:
            rows = [row for row in rows if row.get("project_id") == project_id]
        rows.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
        rows.sort(key=lambda row: row.get("status") not in ACTIVE_STATUSES)
        return copy.deepcopy(rows[:limit])

    def find_active(self, *, project_id: str | None = None, kind: str | None = None) -> dict[str, Any] | None:
        for row in self.rows.values():
            if row.get("status") not in ACTIVE_STATUSES:
                continue
            if project_id is not None and row.get("project_id") != project_id:
                continue
            if kind is not None and row.get("kind") != kind:
                continue
            return copy.deepcopy(row)
        return None

    def recover_interrupted(self) -> int:
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in self.rows.values():
            if row.get("status") in ACTIVE_STATUSES:
                row.update(status="INTERRUPTED", active_key=None, finished_at=now, updated_at=now,
                           error="Application stopped before the job completed",
                           error_category="worker_interrupted", current_message="Interrupted by restart")
                count += 1
        return count


def _serialize(model) -> dict[str, Any]:
    def iso(value):
        return value.isoformat() if isinstance(value, datetime) else value

    return {column.name: iso(getattr(model, column.name)) for column in model.__table__.columns}


class DatabaseJobStore:
    """PostgreSQL-backed store; unique active keys coordinate API processes."""

    def __init__(self, database=None):
        self._database = database

    def _session(self):
        if self._database is not None:
            return self._database.session()
        from mkb.db.engine import SyncSessionLocal
        return SyncSessionLocal()

    def create(self, row: dict[str, Any]) -> dict[str, Any]:
        from mkb.db.models import BackgroundJob
        values = dict(row)
        values["job_id"] = uuid.UUID(values["job_id"])
        for key in ("created_at", "queued_at", "started_at", "finished_at", "updated_at"):
            if isinstance(values.get(key), str):
                values[key] = datetime.fromisoformat(values[key])
        for key in ("events", "result"):
            values[key] = _json_safe(values.get(key))
        with self._session() as session:
            model = BackgroundJob(**values)
            session.add(model)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                predicates = []
                if row.get("idempotency_key"):
                    predicates.append(BackgroundJob.idempotency_key == row["idempotency_key"])
                if row.get("active_key"):
                    predicates.append(BackgroundJob.active_key == row["active_key"])
                if not predicates:
                    raise
                query = select(BackgroundJob).where(or_(*predicates))
                existing = session.scalar(query)
                if existing is not None:
                    raise JobConflict(_serialize(existing)) from exc
                raise
            session.refresh(model)
            return _serialize(model)

    def update(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        from mkb.db.models import BackgroundJob
        with self._session() as session:
            model = session.get(BackgroundJob, uuid.UUID(job_id))
            if model is None:
                return None
            for key, value in changes.items():
                if key in {"created_at", "queued_at", "started_at", "finished_at", "updated_at"} and isinstance(value, str):
                    value = datetime.fromisoformat(value)
                if key in {"events", "result"}:
                    value = _json_safe(value)
                setattr(model, key, value)
            session.commit()
            session.refresh(model)
            return _serialize(model)

    def get(self, job_id: str) -> dict[str, Any] | None:
        from mkb.db.models import BackgroundJob
        with self._session() as session:
            model = session.get(BackgroundJob, uuid.UUID(job_id))
            return _serialize(model) if model else None

    def list(self, *, limit: int, project_id: str | None = None) -> list[dict[str, Any]]:
        from mkb.db.models import BackgroundJob
        with self._session() as session:
            query = select(BackgroundJob)
            if project_id is not None:
                query = query.where(BackgroundJob.project_id == project_id)
            query = query.order_by(BackgroundJob.updated_at.desc()).limit(max(1, min(limit, 500)))
            rows = [_serialize(row) for row in session.scalars(query)]
        rows.sort(key=lambda row: row["status"] not in ACTIVE_STATUSES)
        return rows

    def find_active(self, *, project_id: str | None = None, kind: str | None = None) -> dict[str, Any] | None:
        from mkb.db.models import BackgroundJob
        with self._session() as session:
            query = select(BackgroundJob).where(BackgroundJob.status.in_(ACTIVE_STATUSES))
            if project_id is not None:
                query = query.where(BackgroundJob.project_id == project_id)
            if kind is not None:
                query = query.where(BackgroundJob.kind == kind)
            model = session.scalar(query.order_by(BackgroundJob.created_at).limit(1))
            return _serialize(model) if model else None

    def recover_interrupted(self) -> int:
        from mkb.db.models import BackgroundJob
        now = datetime.now(timezone.utc)
        with self._session() as session:
            rows = list(session.scalars(select(BackgroundJob).where(BackgroundJob.status.in_(ACTIVE_STATUSES))))
            for row in rows:
                row.status = "INTERRUPTED"
                row.active_key = None
                row.finished_at = now
                row.error = "Application stopped before the job completed"
                row.error_category = "worker_interrupted"
                row.current_message = "Interrupted by restart"
            session.commit()
            return len(rows)
