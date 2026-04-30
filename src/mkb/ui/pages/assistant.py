"""Assistant page — conversational orchestrator agent for the MKB pipeline."""

from __future__ import annotations

import streamlit as st

from mkb import api
from mkb.ui.background_jobs import get_running_job, start_job

# Session state keys
_KEY_RUNNER = "_orch_runner"
_KEY_SESSION = "_orch_session_id"
_KEY_HISTORY = "_orch_history"
_KEY_JOB_ID = "_orch_job_id"


def _init_session() -> None:
    """Initialize the orchestrator runner and session (once per browser session)."""
    if _KEY_RUNNER not in st.session_state:
        from mkb.agents.orchestrator import create_orchestrator_runner

        runner, session_id = create_orchestrator_runner()
        st.session_state[_KEY_RUNNER] = runner
        st.session_state[_KEY_SESSION] = session_id
        st.session_state[_KEY_HISTORY] = []
        st.session_state[_KEY_JOB_ID] = None


def _dispatch_pending_workflows() -> None:
    """Drain the orchestrator workflow queue and start background jobs for each."""
    from mkb.agents.tools.orchestrator_tools import get_pending_workflows

    pending = get_pending_workflows()
    for req in pending:
        kind = req["kind"]
        project_id = req.get("project_id")
        kwargs = req.get("kwargs", {})
        label = req.get("label", kind)

        if kind == "extraction":
            start_job(
                kind="extraction",
                label=label,
                project_id=project_id,
                target=api.extract,
                kwargs=kwargs,
            )
        elif kind == "projection":
            start_job(
                kind="projection",
                label=label,
                project_id=project_id,
                target=api.project,
                kwargs={"space_id": kwargs["space_id"], "project_id": kwargs["project_id"]},
            )
        elif kind == "kg_extraction":
            start_job(
                kind="kg_extraction",
                label=label,
                project_id=project_id,
                target=api.extract_knowledge_graph,
                kwargs={"project_id": kwargs["project_id"]},
            )
        elif kind == "feedback_review":
            start_job(
                kind="feedback_review",
                label=label,
                project_id=project_id,
                target=api.review_feedback,
                kwargs={"project_id": kwargs["project_id"]},
            )
        elif kind == "projection_review":
            start_job(
                kind="projection_review",
                label=label,
                project_id=project_id,
                target=api.review_projections,
                kwargs={"space_id": kwargs["space_id"], "project_id": kwargs["project_id"]},
            )


def _collect_reply() -> None:
    """Check if the current orchestrator job has completed and add reply to history."""
    job_id = st.session_state.get(_KEY_JOB_ID)
    if not job_id:
        return

    from mkb.ui.background_jobs import _init_job_state

    _init_job_state()
    job = st.session_state.background_jobs.get(job_id)
    if not job:
        st.session_state[_KEY_JOB_ID] = None
        return

    if job["status"] == "COMPLETED":
        result = job.get("result") or {}
        reply = result.get("reply") or ""
        if not reply and job.get("error"):
            reply = f"An error occurred: {job['error']}"
        elif not reply:
            reply = "(No response)"
        st.session_state[_KEY_HISTORY].append({"role": "assistant", "content": reply})
        st.session_state[_KEY_JOB_ID] = None
    elif job["status"] == "FAILED":
        error = job.get("error") or "Unknown error"
        st.session_state[_KEY_HISTORY].append(
            {"role": "assistant", "content": f"Sorry, an error occurred: {error}"}
        )
        st.session_state[_KEY_JOB_ID] = None


def _is_thinking() -> bool:
    """Return True if the orchestrator is currently processing a message."""
    job_id = st.session_state.get(_KEY_JOB_ID)
    if not job_id:
        return False
    from mkb.ui.background_jobs import _init_job_state

    _init_job_state()
    job = st.session_state.background_jobs.get(job_id)
    return bool(job and job["status"] == "RUNNING")


def _render_chat_history() -> None:
    history = st.session_state.get(_KEY_HISTORY, [])
    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def _render_thinking_indicator() -> None:
    job_id = st.session_state.get(_KEY_JOB_ID)
    if not job_id:
        return
    from mkb.ui.background_jobs import _init_job_state

    _init_job_state()
    job = st.session_state.background_jobs.get(job_id)
    if job and job["status"] == "RUNNING":
        with st.chat_message("assistant"):
            current = job.get("current_message") or "Thinking..."
            st.markdown(f"*{current}*")


def render() -> None:
    st.header("Assistant")
    st.caption("Ask me to check your projects, run workflows, or explain the system state.")

    _init_session()
    _collect_reply()
    _dispatch_pending_workflows()

    # Controls row
    col1, col2 = st.columns([6, 1])
    with col2:
        if st.button("Clear", use_container_width=True):
            from mkb.agents.orchestrator import create_orchestrator_runner

            runner, session_id = create_orchestrator_runner()
            st.session_state[_KEY_RUNNER] = runner
            st.session_state[_KEY_SESSION] = session_id
            st.session_state[_KEY_HISTORY] = []
            st.session_state[_KEY_JOB_ID] = None
            st.rerun()

    # Chat history + thinking indicator
    _render_chat_history()
    _render_thinking_indicator()

    # Chat input — disabled while the agent is thinking
    thinking = _is_thinking()
    placeholder = "Thinking..." if thinking else "Tell me what to do..."

    if prompt := st.chat_input(placeholder, disabled=thinking):
        st.session_state[_KEY_HISTORY].append({"role": "user", "content": prompt})

        runner = st.session_state[_KEY_RUNNER]
        session_id = st.session_state[_KEY_SESSION]

        from mkb.agents.orchestrator import send_message

        job_id = start_job(
            kind="orchestrator_chat",
            label="Assistant",
            target=send_message,
            kwargs={
                "runner": runner,
                "session_id": session_id,
                "message": prompt,
            },
        )
        st.session_state[_KEY_JOB_ID] = job_id
        st.rerun()
