from fastapi import APIRouter, HTTPException

from mkb.web.dependencies import get_knowledge_base
from mkb.web.job_backend import serialize_web_job
from mkb.web._models import ReviewJobChatRequest

router = APIRouter()


@router.get("/api/jobs")
def list_jobs(limit: int = 100):
    return [serialize_web_job(job) for job in get_knowledge_base().jobs.list(limit=limit)]


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = get_knowledge_base().jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_web_job(job)


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    service = get_knowledge_base().jobs
    job = service.get(job_id)
    if job is None or job.status not in {"QUEUED", "RUNNING", "CANCELLING"}:
        raise HTTPException(status_code=404, detail="Job not found or not cancellable")
    service.cancel(job.id)
    return {"ok": True}


@router.post("/api/jobs/cancel-all")
def cancel_all_jobs(project_id: str | None = None):
    cancelled = get_knowledge_base().jobs.cancel_all(project_id=project_id)
    identifiers = [str(job.id) for job in cancelled]
    return {"ok": True, "cancelled": identifiers, "count": len(identifiers)}


@router.post("/api/jobs/{job_id}/review-chat")
def review_job_chat(job_id: str, body: ReviewJobChatRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    job = get_knowledge_base().jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    row = serialize_web_job(job)
    if row.get("kind") != "projection_review":
        raise HTTPException(status_code=400, detail="Follow-up chat is only available for projection review jobs")
    if row.get("status") not in {"COMPLETED", "FAILED"}:
        raise HTTPException(status_code=400, detail="Wait for the review job to finish before sending a follow-up")

    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    space_id = result.get("space_id")
    project_id = result.get("project_id") or row.get("project_id")
    if not space_id or not project_id:
        raise HTTPException(
            status_code=400,
            detail="This review job does not identify a single space/project for follow-up chat",
        )

    followup_job = get_knowledge_base().jobs.submit_action(
        "review_projection_followup",
        job_project_id=str(project_id),
        space_id=str(space_id),
        project_id=str(project_id),
        message=message,
        previous_job=row,
        reviewer_id=result.get("reviewer_id"),
    )
    return {"job_id": str(followup_job.id)}
