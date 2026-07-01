from fastapi import APIRouter, HTTPException

from mkb.web._helpers import start_web_job_action
from mkb.web._models import ReviewJobChatRequest
from mkb.web._state import jobs

router = APIRouter()


@router.get("/api/jobs")
def list_jobs(limit: int = 100):
    return jobs.list_jobs(limit=limit)


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    row = jobs.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    ok = jobs.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found or not cancellable")
    return {"ok": True}


@router.post("/api/jobs/cancel-all")
def cancel_all_jobs(project_id: str | None = None):
    cancelled = jobs.cancel_all_active(project_id=project_id)
    return {"ok": True, "cancelled": cancelled, "count": len(cancelled)}


@router.post("/api/jobs/{job_id}/review-chat")
def review_job_chat(job_id: str, body: ReviewJobChatRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    row = jobs.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
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

    followup_job_id = start_web_job_action(
        jobs,
        "review_projection_followup",
        job_project_id=str(project_id),
        space_id=str(space_id),
        project_id=str(project_id),
        message=message,
        previous_job=row,
        reviewer_id=result.get("reviewer_id"),
    )
    return {"job_id": followup_job_id}
