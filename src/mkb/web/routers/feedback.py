from fastapi import APIRouter

from mkb.web._helpers import _parse_uuid
from mkb.web.dependencies import get_knowledge_base
from mkb.web._models import FeedbackResolveRequest, FeedbackReviewRequest

router = APIRouter()


@router.get("/api/feedback")
def list_feedback(limit: int = 100, status: str | None = None, project_id: str | None = None):
    rows = get_knowledge_base().materials.feedback.list(
        project_id=project_id, status=status
    )
    return rows[:limit]


@router.get("/api/feedback/summary/{project_id}")
def feedback_summary(project_id: str):
    return get_knowledge_base().materials.feedback.summary(project_id)


@router.post("/api/feedback/{feedback_id}/resolve")
def resolve_feedback(feedback_id: str, body: FeedbackResolveRequest):
    return get_knowledge_base().materials.feedback.resolve(
        feedback_id=feedback_id, status=body.status, notes=body.notes
    )


@router.post("/api/feedback/review")
def review_feedback(body: FeedbackReviewRequest):
    service = get_knowledge_base().jobs
    if body.project_id:
        _parse_uuid(body.project_id, "project_id")
        job = service.submit_action(
            "review_feedback",
            job_project_id=body.project_id,
            project_id=body.project_id,
        )
    else:
        job = service.submit_action("review_feedback_all")
    return {"job_id": str(job.id)}
