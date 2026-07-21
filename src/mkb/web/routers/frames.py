from fastapi import APIRouter, HTTPException

from mkb.web.dependencies import get_knowledge_base

router = APIRouter()


@router.get("/api/frames")
def list_frames():
    return get_knowledge_base().materials.frames.list()


@router.get("/api/frames/{project_id}")
def get_frame(project_id: str):
    frame = get_knowledge_base().materials.frames.get(project_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    return frame


@router.get("/api/frames/{project_id}/history")
def get_frame_history(project_id: str):
    return get_knowledge_base().materials.frames.history(project_id)
