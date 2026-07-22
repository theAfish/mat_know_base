from fastapi import APIRouter
from fastapi.responses import JSONResponse

from mkb.web.diagnostics import readiness_report

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/live")
def liveness() -> dict[str, str]:
    """Process-only probe; dependency failures must not affect liveness."""
    return {"status": "alive"}


@router.get("/health/ready")
def readiness():
    report = readiness_report()
    if report["status"] != "ready":
        return JSONResponse(report, status_code=503)
    return report
