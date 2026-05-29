from fastapi import APIRouter, HTTPException

from mkb import api

router = APIRouter()


@router.get("/api/frames")
def list_frames():
    return api.list_frames()


@router.get("/api/frames/{project_id}")
def get_frame(project_id: str):
    frame = api.get_frame(project_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    return frame


@router.get("/api/frames/{project_id}/history")
def get_frame_history(project_id: str):
    return api.get_extraction_history(project_id)
