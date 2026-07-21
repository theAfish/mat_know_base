from fastapi import APIRouter, HTTPException

from mkb.logging_setup import setup_logging
from mkb.web.dependencies import get_knowledge_base
from mkb.web._models import SettingsUpdateRequest

router = APIRouter()


@router.get("/api/settings")
def get_settings_endpoint():
    return get_knowledge_base().settings.runtime()


@router.put("/api/settings")
def update_settings_endpoint(body: SettingsUpdateRequest):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        result = get_knowledge_base().settings.update(updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Apply log level change immediately without a server restart.
    if "log_level" in updates:
        setup_logging(level=result.get("log_level"), force=True)

    return result
