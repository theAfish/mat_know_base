"""Feedback API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    init_db,
    uuid,
)


def list_feedback(
    project_id: str | uuid.UUID | None = None,
    status: str | None = None,
) -> list[dict]:
    """List feedback items, optionally filtered by project and/or status."""
    from mkb.feedback.manager import list_feedback as _list

    pid = uuid.UUID(str(project_id)) if project_id else None
    return _list(project_id=pid, status=status)

def review_feedback(
    project_id: str | uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
) -> dict:
    """Run feedback review on a project — KB agent reviews and resolves open feedback."""
    from mkb.agents.feedback_reviewer import run_feedback_review

    pid = uuid.UUID(str(project_id))
    if progress_callback:
        progress_callback({"message": f"Reviewing feedback for project {str(pid)[:8]}"})
    return run_feedback_review(pid, model=model, verbose=verbose)

def review_projections(
    space_id: str | uuid.UUID,
    project_id: str | uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    reviewer_id: str | None = None,
) -> dict:
    """Run projection review — consolidate and correct all projections for a project.

    A strict reviewer agent compares all projection runs, cross-references
    against the knowledge frame and source material, and produces a single
    reviewed (consolidated, corrected) projection.
    """
    from mkb.agents.projection_reviewer import run_projection_review

    init_db()
    sid = uuid.UUID(str(space_id))
    pid = uuid.UUID(str(project_id))
    if progress_callback:
        progress_callback({"message": f"Starting post-processor for project {str(pid)[:8]}"})
    return run_projection_review(
        sid,
        pid,
        model=model,
        verbose=verbose,
        progress_callback=progress_callback,
        reviewer_id=reviewer_id,
    )

def review_projections_all(
    space_id: str | uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    project_ids: list[str] | None = None,
    reviewer_id: str | None = None,
) -> dict:
    """Run projection review on projects in a space.

    Args:
        project_ids: Restrict to these projects (per-project, separate
            sessions). When None, reviews every project that has at least
            one completed projection in the space.
    """
    from mkb.agents.projection_reviewer import run_projection_review_all

    init_db()
    sid = uuid.UUID(str(space_id))
    pids = [uuid.UUID(str(p)) for p in project_ids] if project_ids else None
    return run_projection_review_all(
        sid,
        model=model,
        verbose=verbose,
        progress_callback=progress_callback,
        project_ids=pids,
        reviewer_id=reviewer_id,
    )

def review_projections_session(
    space_id: str | uuid.UUID,
    project_ids: list[str],
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    reviewer_id: str | None = None,
) -> dict:
    """Run a SINGLE reviewer session over multiple selected projects.

    One agent context sees every selected project's projections in turn
    and saves each reviewed result before moving on to the next.
    """
    from mkb.agents.projection_reviewer import run_projection_review_session

    init_db()
    sid = uuid.UUID(str(space_id))
    pids = [uuid.UUID(str(p)) for p in project_ids]
    if not pids:
        return {"status": "error", "message": "project_ids is required for session mode"}
    return run_projection_review_session(
        sid,
        pids,
        model=model,
        verbose=verbose,
        progress_callback=progress_callback,
        reviewer_id=reviewer_id,
    )

def review_projection_followup(
    space_id: str | uuid.UUID,
    project_id: str | uuid.UUID,
    message: str,
    previous_job: dict | None = None,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    reviewer_id: str | None = None,
) -> dict:
    """Run a follow-up turn for a completed projection review job."""
    from mkb.agents.projection_reviewer import run_projection_review_followup

    init_db()
    sid = uuid.UUID(str(space_id))
    pid = uuid.UUID(str(project_id))
    if progress_callback:
        progress_callback({"message": "Starting review follow-up"})
    return run_projection_review_followup(
        sid,
        pid,
        message,
        previous_job=previous_job,
        model=model,
        verbose=verbose,
        progress_callback=progress_callback,
        reviewer_id=reviewer_id,
    )

def get_feedback_summary(
    project_id: str | uuid.UUID,
) -> dict:
    """Get counts of feedback by category and status for a project."""
    from mkb.feedback.manager import get_feedback_summary as _summary

    pid = uuid.UUID(str(project_id))
    return _summary(pid)

def resolve_feedback(
    feedback_id: str | uuid.UUID,
    status: str,
    notes: str,
) -> dict:
    """Manually resolve a feedback item."""
    from mkb.feedback.manager import resolve_feedback as _resolve

    fid = uuid.UUID(str(feedback_id))
    return _resolve(fid, status=status, notes=notes, resolved_by="user")

