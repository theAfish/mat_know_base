from fastapi import APIRouter, HTTPException

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
