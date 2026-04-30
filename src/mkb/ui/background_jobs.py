"""Shared background-job helpers for the Streamlit UI."""

from __future__ import annotations

import queue
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import streamlit as st


_EVENT_LIMIT = 25
_HISTORY_LIMIT = 12

_STATUS_ICON = {
    "QUEUED": "⏳",
    "RUNNING": "🟡",
    "COMPLETED": "🟢",
    "FAILED": "🔴",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_job_state() -> None:
    if "background_jobs" not in st.session_state:
        st.session_state.background_jobs = {}
    if "background_job_queues" not in st.session_state:
        st.session_state.background_job_queues = {}


def _trim_history() -> None:
    jobs = st.session_state.background_jobs
    completed_ids = [
        job_id
        for job_id, job in jobs.items()
        if job["status"] in {"COMPLETED", "FAILED"}
    ]
    if len(completed_ids) <= _HISTORY_LIMIT:
        return

    completed_ids.sort(key=lambda job_id: jobs[job_id].get("finished_at") or "")
    for job_id in completed_ids[:-_HISTORY_LIMIT]:
        jobs.pop(job_id, None)
        st.session_state.background_job_queues.pop(job_id, None)


def has_running_jobs() -> bool:
    _init_job_state()
    return any(job["status"] == "RUNNING" for job in st.session_state.background_jobs.values())


def get_project_jobs(project_id: str) -> list[dict[str, Any]]:
    _init_job_state()
    jobs = [
        job for job in st.session_state.background_jobs.values()
        if job.get("project_id") == project_id
    ]
    jobs.sort(key=lambda job: job.get("started_at") or "", reverse=True)
    return jobs


def get_running_job(project_id: str, job_kind: str | None = None) -> dict[str, Any] | None:
    for job in get_project_jobs(project_id):
        if job["status"] != "RUNNING":
            continue
        if job_kind is None or job["kind"] == job_kind:
            return job
    return None


def start_job(
    *,
    kind: str,
    label: str,
    target: Callable[..., Any],
    project_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    args: tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
) -> str:
    """Start a daemon-thread background job and register progress state."""
    _init_job_state()

    job_id = str(uuid.uuid4())
    progress_queue: queue.Queue = queue.Queue()
    st.session_state.background_job_queues[job_id] = progress_queue
    st.session_state.background_jobs[job_id] = {
        "job_id": job_id,
        "kind": kind,
        "label": label,
        "project_id": project_id,
        "metadata": metadata or {},
        "status": "RUNNING",
        "started_at": _now_iso(),
        "finished_at": None,
        "current_message": "Queued",
        "events": [],
        "result": None,
        "error": None,
        "toast_sent": False,
    }

    worker_args = args or ()
    worker_kwargs = dict(kwargs or {})

    def _emit_progress(event: dict[str, Any]) -> None:
        progress_queue.put({"type": "progress", **event})

    def _run() -> None:
        try:
            _emit_progress({"message": f"Started {label.lower()}"})
            if "progress_callback" not in worker_kwargs:
                worker_kwargs["progress_callback"] = _emit_progress
            result = target(*worker_args, **worker_kwargs)
            progress_queue.put({"type": "done", "result": result})
        except Exception as exc:  # noqa: BLE001
            progress_queue.put({"type": "error", "error": str(exc)})

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def poll_jobs() -> bool:
    """Drain all background-job queues. Returns True if any state changed."""
    _init_job_state()
    changed = False

    for job_id, progress_queue in list(st.session_state.background_job_queues.items()):
        job = st.session_state.background_jobs.get(job_id)
        if job is None:
            st.session_state.background_job_queues.pop(job_id, None)
            continue

        while True:
            try:
                event = progress_queue.get_nowait()
            except queue.Empty:
                break

            changed = True
            event_type = event.get("type")
            if event_type == "progress":
                message = event.get("message") or event.get("label") or "Working"
                job["current_message"] = message
                progress_event = {
                    "message": message,
                    "timestamp": _now_iso(),
                }
                for key in ["label", "tool", "stage", "status", "asset_id", "filename"]:
                    if key in event:
                        progress_event[key] = event[key]
                job["events"].append(progress_event)
                if len(job["events"]) > _EVENT_LIMIT:
                    job["events"] = job["events"][-_EVENT_LIMIT:]
            elif event_type == "done":
                job["status"] = "COMPLETED"
                job["result"] = event.get("result")
                job["finished_at"] = _now_iso()
                result = event.get("result")
                if isinstance(result, dict):
                    summary = result.get("message") or result.get("status") or "Completed"
                else:
                    summary = "Completed"
                job["current_message"] = str(summary)
                st.session_state.background_job_queues.pop(job_id, None)
            elif event_type == "error":
                job["status"] = "FAILED"
                job["error"] = event.get("error") or "Unknown error"
                job["finished_at"] = _now_iso()
                job["current_message"] = job["error"]
                st.session_state.background_job_queues.pop(job_id, None)

    _trim_history()
    return changed


def render_sidebar_monitor() -> None:
    _init_job_state()
    jobs = list(st.session_state.background_jobs.values())
    jobs.sort(key=lambda job: job.get("started_at") or "", reverse=True)

    with st.sidebar.expander("Background Jobs", expanded=has_running_jobs()):
        if not jobs:
            st.caption("No background jobs.")
        for job in jobs:
            icon = _STATUS_ICON.get(job["status"], "•")
            title = f"{icon} {job['label']}"
            if job.get("project_id"):
                title += f" · {job['project_id'][:8]}"
            st.write(title)
            st.caption(job.get("current_message") or job["status"])

            recent_events = job.get("events") or []
            if recent_events:
                for event in reversed(recent_events[-3:]):
                    st.caption(f"• {event['message']}")

            if job["status"] == "FAILED" and job.get("error"):
                st.caption(f"Error: {job['error']}")

            if job["status"] in {"COMPLETED", "FAILED"} and not job.get("toast_sent"):
                if job["status"] == "COMPLETED":
                    st.toast(f"{job['label']} finished", icon="✅")
                else:
                    st.toast(f"{job['label']} failed", icon="⚠️")
                job["toast_sent"] = True


def render_project_job_status(project_id: str) -> None:
    jobs = get_project_jobs(project_id)
    if not jobs:
        return

    active_jobs = [job for job in jobs if job["status"] == "RUNNING"]
    if active_jobs:
        st.info("Background work is running for this project. You can navigate away and come back later.")

    for job in jobs[:3]:
        icon = _STATUS_ICON.get(job["status"], "•")
        with st.expander(f"{icon} {job['label']} · {job['status']}", expanded=job["status"] == "RUNNING"):
            st.caption(job.get("current_message") or job["status"])
            events = job.get("events") or []
            if events:
                for event in reversed(events[-8:]):
                    st.caption(f"• {event['message']}")
            if job["status"] == "FAILED" and job.get("error"):
                st.error(job["error"])


def auto_refresh_if_running(interval_seconds: float = 0.6) -> None:
    """Register a non-blocking fragment timer that triggers a full rerun while jobs run.

    Uses ``st.fragment(run_every=...)`` so that the browser-side timer fires the
    refresh without blocking the Streamlit script thread.  This lets page
    navigation happen immediately instead of waiting for the sleep to expire.
    """
    @st.fragment(run_every=interval_seconds)
    def _auto_refresh() -> None:
        if has_running_jobs():
            st.rerun()

    _auto_refresh()