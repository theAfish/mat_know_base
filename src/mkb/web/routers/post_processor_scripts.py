from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from mkb.web._helpers import _parse_uuid, require_service_result
from mkb.web.dependencies import get_knowledge_base


router = APIRouter()


@router.get("/api/post-processor-scripts")
def list_post_processor_scripts():
    return [
        _serialize_processor(processor)
        for processor in get_knowledge_base().post_processors.list(limit=1000)
    ]


@router.post("/api/post-processor-scripts/upload")
async def upload_post_processor_script(file: UploadFile = File(...)):
    kb = get_knowledge_base()
    try:
        if not kb.config.allow_uploaded_python:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Uploaded Python post-processors are disabled. A trusted "
                    "administrator must explicitly enable them and restart MKB."
                ),
            )
        limit = kb.config.upload_max_file_mb * 1024 * 1024
        content = file.file.read(limit + 1)
        if len(content) > limit:
            raise ValueError("Post-processor script exceeds the upload byte limit")
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Post-processor script must be UTF-8 text") from exc
        processor = kb.post_processors.register(
            name=Path(file.filename or "processor.py").stem,
            filename=file.filename or "processor.py",
            source=source,
        )
        return _serialize_processor(processor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/post-processor-scripts/{script_id}")
def delete_post_processor_script(script_id: str):
    _parse_uuid(script_id, "script_id")
    service = get_knowledge_base().post_processors
    processor = service.get(script_id)
    if processor is None:
        raise HTTPException(status_code=404, detail="Post-processor script not found")
    service.delete(processor.id)
    return require_service_result({"ok": True, "deleted": processor.name})


def _serialize_processor(processor) -> dict:
    return {
        "script_id": str(processor.id),
        "name": processor.name,
        "filename": processor.filename,
        "created_at": (
            processor.created_at.isoformat() if processor.created_at else None
        ),
    }
