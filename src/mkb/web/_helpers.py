"""Small reusable web-layer helpers (input validation, path safety)."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException
from mkb.services.ids import strict_uuid
from mkb.services.result import ServiceError, is_error_result, result_status_code
from mkb.web.job_actions import JobActionConflict, start_job_action


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return strict_uuid(value, field)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}: {value!r}") from exc


def _safe_child(base: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``base``, rejecting absolute paths and zip-slip."""
    p = Path(rel)
    if p.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute path rejected")
    full = (base / p).resolve()
    try:
        full.relative_to(base.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path traversal rejected") from exc
    return full


def service_http_exception(
    error: ServiceError | dict,
    *,
    default_status: int = 400,
) -> HTTPException:
    if isinstance(error, ServiceError):
        return HTTPException(status_code=error.status_code, detail=error.to_dict())
    return HTTPException(
        status_code=result_status_code(error, default_status),
        detail=error.get("error") or error,
    )


def require_service_result(result, *, default_status: int = 400):
    """Return successful service results or raise a normalized HTTP error."""
    if isinstance(result, ServiceError):
        raise service_http_exception(result, default_status=default_status)
    if is_error_result(result):
        raise service_http_exception(result, default_status=default_status)
    return result


def require_service_result_or_not_found(result, *, default_status: int = 400):
    """Map service errors containing 'not found' to 404, otherwise default."""
    if is_error_result(result) and "not found" in str(result.get("error", "")).lower():
        default_status = 404
    return require_service_result(result, default_status=default_status)


def start_web_job_action(manager, action: str, **kwargs) -> str:
    try:
        return start_job_action(manager, action, **kwargs)
    except JobActionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
