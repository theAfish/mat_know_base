from fastapi import APIRouter, HTTPException

from mkb.logging_setup import setup_logging
from mkb.web._models import SettingsUpdateRequest

router = APIRouter()


@router.get("/api/settings")
def get_settings_endpoint():
    from mkb import runtime_settings

    return runtime_settings.public_view()


@router.put("/api/settings")
def update_settings_endpoint(body: SettingsUpdateRequest):
    from mkb import runtime_settings

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        result = runtime_settings.update_settings(updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Apply log level change immediately without a server restart.
    if "log_level" in updates:
        setup_logging(level=result.get("log_level"), force=True)

    return runtime_settings.public_view(result)
