from fastapi import APIRouter, File, HTTPException, UploadFile

from mkb import ConflictError, ValidationError
from mkb.web._helpers import _parse_uuid, require_service_result
from mkb.web.dependencies import get_knowledge_base

router = APIRouter()


@router.get("/api/skills")
def list_skills():
    return [_serialize_skill(skill) for skill in get_knowledge_base().skills.list(limit=1000)]


@router.get("/api/skills/{skill_id_or_slug}")
def get_skill(skill_id_or_slug: str):
    skill = get_knowledge_base().skills.get(skill_id_or_slug)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _serialize_skill(skill, include_content=True)


@router.post("/api/skills/upload")
async def upload_skill(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    try:
        skill = get_knowledge_base().skills.import_files(
            [(upload.filename or "", upload.file) for upload in files]
        )
        return _serialize_skill(skill, include_content=True)
    except (ValueError, ConflictError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str):
    _parse_uuid(skill_id, "skill_id")
    service = get_knowledge_base().skills
    skill = service.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    service.delete(skill.id)
    return require_service_result({"ok": True, "deleted": skill.name})


def _serialize_skill(skill, *, include_content: bool = False) -> dict:
    payload = {
        "skill_id": str(skill.id),
        "name": skill.name,
        "slug": skill.slug,
        "description": skill.description,
        "metadata": skill.metadata,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }
    if include_content:
        payload["skill_md"] = skill.content
    return payload
