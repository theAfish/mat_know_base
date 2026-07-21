"""Shared web-layer state: job manager and assistant session singletons.

This module is imported by routers under ``mkb.web.routers`` so they all
observe the same ``jobs`` and ``assistant_session`` instances. Do NOT
import any router from here (would create cycles).
"""
from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mkb.agents._utils import JobCancelled
from mkb.agents.orchestrator import create_orchestrator_runner
from mkb.agents.tools.orchestrator_tools import get_pending_workflows
from mkb.config import settings
from mkb.jobs import DatabaseJobStore, JobStore, MemoryJobStore
from mkb.web.job_actions import action_for_workflow_kind, start_job_action

_EVENT_LIMIT = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_result_error(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    status = str(result.get("status") or "").strip().lower()
    if status in {"error", "failed"}:
        return str(result.get("message") or result.get("error") or "Job returned an error result.")
    return None


def _job_result_message(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    for key in ("message", "agent_summary", "status"):
        value = result.get(key)
        if value:
            return str(value)
    return None


@dataclass
class AssistantSession:
    runner: Any
    session_id: str


class JobManager:
    def __init__(self, max_concurrent: int | None = None, store: JobStore | None = None) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._queues: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()
        self._store = store or MemoryJobStore()
        limit = max_concurrent if max_concurrent is not None else settings.max_concurrent_jobs
        self._semaphore = threading.Semaphore(max(1, limit))

    def bind_store(self, store: JobStore) -> None:
        """Bind persistence before the server begins accepting jobs."""
        with self._lock:
            if self._queues:
                raise RuntimeError("Cannot replace the job store while jobs are active")
            self._store = store

    def start_job(
        self,
        *,
        kind: str,
        label: str,
        target,
        project_id: str | None = None,
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        active_key: str | None = None,
        retryable: bool = False,
        max_attempts: int = 1,
    ) -> str:
        job_id = str(uuid.uuid4())
        q: queue.Queue = queue.Queue()

        now = _now_iso()
        row = {
            "job_id": job_id,
            "kind": kind,
            "label": label,
            "status": "QUEUED",
            "project_id": project_id,
            "request_id": None,
            "idempotency_key": idempotency_key,
            "active_key": active_key,
            "attempt_count": 0,
            "max_attempts": max(1, max_attempts),
            "retryable": retryable,
            "cancel_requested": False,
            "result": None,
            "error": None,
            "error_category": None,
            "current_message": "Queued",
            "events": [],
            "created_at": now,
            "queued_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
        }
        try:
            from mkb.web.request_context import request_id_var
            row["request_id"] = request_id_var.get()
        except ImportError:
            pass
        created = self._store.create(row)
        with self._lock:
            self._queues[job_id] = q
            self._jobs[job_id] = created

        worker_args = args or ()
        worker_kwargs = dict(kwargs or {})

        def progress_callback(event: dict[str, Any] | str) -> None:
            # Cooperative cancellation: raise before queuing any more work so
            # the worker unwinds at the next inter-step boundary.
            persisted = self._store.get(job_id)
            if job_id in self._cancelled or (persisted and persisted.get("cancel_requested")):
                raise JobCancelled()
            if isinstance(event, str):
                q.put({"type": "progress", "message": event})
            elif isinstance(event, dict):
                q.put({"type": "progress", **event})
            else:
                q.put({"type": "progress", "message": str(event)})

        if "progress_callback" not in worker_kwargs:
            worker_kwargs["progress_callback"] = progress_callback

        def runner() -> None:
            self._semaphore.acquire()
            try:
                if job_id in self._cancelled:
                    q.put({"type": "cancelled"})
                    return
                q.put({"type": "running"})
                q.put({"type": "progress", "message": f"Started {label.lower()}"})
                result = target(*worker_args, **worker_kwargs)
                q.put({"type": "done", "result": result})
            except JobCancelled:
                q.put({"type": "cancelled"})
            except Exception as exc:  # noqa: BLE001
                q.put({"type": "error", "error": str(exc)})
            finally:
                self._semaphore.release()

        threading.Thread(target=runner, daemon=True).start()
        return job_id

    def _drain(self) -> None:
        with self._lock:
            items = list(self._queues.items())

        for job_id, q in items:
            while True:
                try:
                    event = q.get_nowait()
                except queue.Empty:
                    break

                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is None:
                        continue

                    # Don't let late events resurrect a job the user already
                    # cancelled. We still drain the queue so it can be GC'd.
                    if job.get("status") == "CANCELLED":
                        et_terminal = event.get("type") in ("done", "error", "cancelled")
                        if et_terminal:
                            self._queues.pop(job_id, None)
                        continue

                    et = event.get("type")
                    if et == "running":
                        job["status"] = "RUNNING"
                        job["current_message"] = "Running"
                        job["started_at"] = _now_iso()
                        job["attempt_count"] = int(job.get("attempt_count") or 0) + 1
                    elif et == "progress":
                        message = event.get("message") or event.get("label") or "Working"
                        job["current_message"] = str(message)
                        payload = {"message": str(message), "timestamp": _now_iso()}
                        for key in [
                            "stage",
                            "tool",
                            "action",
                            "label",
                            "element_type",
                            "filename",
                            "asset_id",
                            "payload",
                        ]:
                            if key in event:
                                payload[key] = event[key]
                        job["events"].append(payload)
                        if len(job["events"]) > _EVENT_LIMIT:
                            job["events"] = job["events"][-_EVENT_LIMIT:]
                    elif et == "done":
                        job["result"] = event.get("result")
                        error_message = _job_result_error(job["result"])
                        if error_message:
                            job["status"] = "FAILED"
                            job["error"] = error_message
                            job["current_message"] = error_message
                            job["events"].append(
                                {
                                    "message": error_message,
                                    "timestamp": _now_iso(),
                                    "stage": "error_result",
                                    "payload": job["result"],
                                }
                            )
                        else:
                            job["status"] = "COMPLETED"
                            job["current_message"] = "Completed"
                            result_message = _job_result_message(job["result"])
                            if result_message:
                                job["events"].append(
                                    {
                                        "message": result_message,
                                        "timestamp": _now_iso(),
                                        "stage": "result",
                                        "payload": job["result"],
                                    }
                                )
                        if len(job["events"]) > _EVENT_LIMIT:
                            job["events"] = job["events"][-_EVENT_LIMIT:]
                        self._queues.pop(job_id, None)
                        job["active_key"] = None
                        job["finished_at"] = _now_iso()
                    elif et == "error":
                        job["status"] = "FAILED"
                        job["error"] = event.get("error") or "Unknown error"
                        job["current_message"] = job["error"]
                        self._queues.pop(job_id, None)
                        job["active_key"] = None
                        job["finished_at"] = _now_iso()
                        job["error_category"] = "worker_error"
                    elif et == "cancelled":
                        job["status"] = "CANCELLED"
                        job["current_message"] = "Cancelled"
                        self._queues.pop(job_id, None)
                        job["active_key"] = None
                        job["finished_at"] = _now_iso()
                    job["updated_at"] = _now_iso()
                    self._store.update(job_id, **{key: value for key, value in job.items() if key != "job_id"})

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self._drain()
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                job = self._store.get(job_id)
                if job is not None:
                    self._jobs[job_id] = job
        return dict(job) if job else self._store.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Request cancellation of a QUEUED or RUNNING job.

        Returns True if the job was found and a cancellation was initiated.
        QUEUED jobs are marked CANCELLED immediately; RUNNING jobs observe the
        request at cooperative progress/cancellation checkpoints.
        """
        self._drain()
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            status = job.get("status")
            if status not in ("QUEUED", "RUNNING"):
                return False
            self._cancelled.add(job_id)
            # Mark the job as CANCELLED right away so listings/polls reflect
            # the user's intent immediately. The worker thread will still
            # unwind asynchronously; the drain loop ignores late events for
            # jobs already in a terminal state.
            job["status"] = "CANCELLED" if status == "QUEUED" else "CANCELLING"
            job["cancel_requested"] = True
            if status == "QUEUED":
                job["active_key"] = None
                job["finished_at"] = _now_iso()
            job["current_message"] = "Cancelled"
            job["updated_at"] = _now_iso()
            if status == "QUEUED":
                # Thread is blocked on semaphore — the runner will see the
                # cancellation flag when it wakes up and exit cleanly.
                self._queues.pop(job_id, None)
            self._store.update(job_id, **{key: value for key, value in job.items() if key != "job_id"})
        return True

    def cancel_all_active(self, *, project_id: str | None = None) -> list[str]:
        """Cancel every QUEUED or RUNNING job (optionally scoped by project).

        Returns the list of job_ids that received a cancellation request.
        """
        self._drain()
        with self._lock:
            candidates = [
                jid for jid, j in self._jobs.items()
                if j.get("status") in ("QUEUED", "RUNNING")
                and (project_id is None or j.get("project_id") == project_id)
            ]
        cancelled: list[str] = []
        for jid in candidates:
            if self.cancel_job(jid):
                cancelled.append(jid)
        return cancelled

    def list_jobs(self, *, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        self._drain()
        return self._store.list(limit=limit, project_id=project_id)

    def find_active_job(self, *, project_id: str | None = None, kind: str | None = None) -> dict[str, Any] | None:
        self._drain()
        try:
            return self._store.find_active(project_id=project_id, kind=kind)
        except Exception:
            # Starting the job still requires a successful durable create, so
            # this fallback cannot execute unpersisted work. It only keeps
            # isolated adapters/tests able to inspect their local cache.
            with self._lock:
                for job in self._jobs.values():
                    if job.get("status") not in {"QUEUED", "RUNNING", "CANCELLING"}:
                        continue
                    if project_id is not None and job.get("project_id") != project_id:
                        continue
                    if kind is not None and job.get("kind") != kind:
                        continue
                    return dict(job)
            return None

    def recover_interrupted(self) -> int:
        """Mark work owned by a dead application process explicitly interrupted."""
        return self._store.recover_interrupted()


# ── Singletons shared by every router ────────────────────────────────────────

jobs = JobManager(store=DatabaseJobStore())
assistant_lock = threading.Lock()
assistant_session: AssistantSession | None = None


def _get_assistant_session() -> AssistantSession:
    global assistant_session
    with assistant_lock:
        if assistant_session is None:
            runner, session_id = create_orchestrator_runner()
            assistant_session = AssistantSession(runner=runner, session_id=session_id)
        return assistant_session


def _dispatch_pending_workflows() -> None:
    pending = get_pending_workflows()
    for req in pending:
        kind = req.get("kind", "workflow")
        action = req.get("action") or action_for_workflow_kind(kind)
        pid = req.get("project_id")
        kwargs = req.get("kwargs", {})
        label = req.get("label", kind)

        start_job_action(jobs, action, job_project_id=pid, label=label, **kwargs)
