from fastapi import APIRouter, File, HTTPException, UploadFile

from mkb.post_processors import registry
from mkb.web._helpers import _parse_uuid, require_service_result


router = APIRouter()


@router.get("/api/post-processor-scripts")
def list_post_processor_scripts():
    return registry.list_scripts()


@router.post("/api/post-processor-scripts/upload")
async def upload_post_processor_script(file: UploadFile = File(...)):
    try:
        return registry.create_script(file.filename or "", file.file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/post-processor-scripts/{script_id}")
def delete_post_processor_script(script_id: str):
    _parse_uuid(script_id, "script_id")
    return require_service_result(registry.delete_script(script_id), default_status=404)