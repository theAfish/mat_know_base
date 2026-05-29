from typing import Any

from fastapi import APIRouter

from mkb import api
from mkb.web._helpers import _parse_uuid
from mkb.web._models import FeedbackResolveRequest, FeedbackReviewRequest
from mkb.web._state import jobs

router = APIRouter()


@router.get("/api/feedback")
def list_feedback(limit: int = 100, status: str | None = None, project_id: str | None = None):
    rows = api.list_feedback(project_id=project_id, status=status)
    return rows[:limit]


@router.get("/api/feedback/summary/{project_id}")
def feedback_summary(project_id: str):
    return api.get_feedback_summary(project_id)


@router.post("/api/feedback/{feedback_id}/resolve")
def resolve_feedback(feedback_id: str, body: FeedbackResolveRequest):
    return api.resolve_feedback(feedback_id=feedback_id, status=body.status, notes=body.notes)


def _review_feedback_all(progress_callback=None) -> dict[str, Any]:
    projects = api.list_projects(limit=500)
    results = []
    for p in projects:
        pid = p["project_id"]
        summary = api.get_feedback_summary(pid)
        if int(summary.get("total", 0) or 0) == 0:
            continue
        if progress_callback:
            progress_callback({"message": f"Reviewing feedback for {pid[:8]}"})
        results.append(api.review_feedback(project_id=pid))
    return {"reviewed_projects": len(results), "results": results}


@router.post("/api/feedback/review")
def review_feedback(body: FeedbackReviewRequest):
    if body.project_id:
        _parse_uuid(body.project_id, "project_id")
        job_id = jobs.start_job(
            kind="feedback_review",
            label="Feedback Review",
            project_id=body.project_id,
            target=api.review_feedback,
            kwargs={"project_id": body.project_id},
        )
    else:
        job_id = jobs.start_job(
            kind="feedback_review",
            label="Feedback Review",
            target=_review_feedback_all,
        )
    return {"job_id": job_id}
