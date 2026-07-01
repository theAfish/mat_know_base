from fastapi import APIRouter, File, HTTPException, UploadFile

from mkb.skills import registry
from mkb.web._helpers import _parse_uuid, require_service_result

router = APIRouter()


@router.get("/api/skills")
def list_skills():
    return registry.list_skills()


@router.get("/api/skills/{skill_id_or_slug}")
def get_skill(skill_id_or_slug: str):
    skill = registry.get_skill(skill_id_or_slug, include_content=True)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.post("/api/skills/upload")
async def upload_skill(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    try:
        if len(files) == 1:
            upload = files[0]
            filename = upload.filename or ""
            if filename.lower().endswith(".zip"):
                return registry.create_skill_from_zip(filename, upload.file)
            return registry.create_skill_from_single_file(filename, upload.file)

        payload = []
        for upload in files:
            payload.append((upload.filename or "", upload.file))
        return registry.create_skill_from_files(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str):
    _parse_uuid(skill_id, "skill_id")
    result = registry.delete_skill(skill_id)
    return require_service_result(result, default_status=404)
