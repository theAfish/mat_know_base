from fastapi import APIRouter

from mkb import api
from mkb.web._helpers import _parse_uuid, start_web_job_action
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


@router.post("/api/feedback/review")
def review_feedback(body: FeedbackReviewRequest):
    if body.project_id:
        _parse_uuid(body.project_id, "project_id")
        job_id = start_web_job_action(
            jobs,
            "review_feedback",
            job_project_id=body.project_id,
            project_id=body.project_id,
        )
    else:
        job_id = start_web_job_action(jobs, "review_feedback_all")
    return {"job_id": job_id}
