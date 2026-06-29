from fastapi import APIRouter, HTTPException

from mkb import api
from mkb.web._helpers import _parse_uuid
from mkb.web._models import SpaceCreateRequest, SpaceUpdateRequest

router = APIRouter()


@router.get("/api/spaces")
def list_spaces():
    return api.list_spaces()


@router.get("/api/spaces/_defaults/review-prompt")
def get_default_review_prompt(purpose: str = "tabular_database"):
    """Return the built-in default reviewer prompt for a given space purpose.

    Used by the UI to pre-fill the review-prompt editor with a sensible
    starting point that the user can then customize.
    """
    from mkb.agents.prompts.projection_review import default_review_prompt_for

    return {"purpose": purpose, "review_prompt": default_review_prompt_for(purpose)}


@router.get("/api/spaces/{space_id_or_name}")
def get_space(space_id_or_name: str):
    space = api.get_space(space_id_or_name)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    return space


@router.post("/api/spaces")
def create_space(body: SpaceCreateRequest):
    result = api.create_space(
        name=body.name,
        domain=body.domain,
        extraction_schema=body.extraction_schema,
        system_prompt=body.system_prompt,
        field_descriptions=body.field_descriptions,
        description=body.description,
        purpose=body.purpose,
        review_prompt=body.review_prompt,
        review_trackable=body.review_trackable,
        review_allow_search=body.review_allow_search,
        review_search_tools=body.review_search_tools,
    )
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.put("/api/spaces/{space_id}")
def update_space(space_id: str, body: SpaceUpdateRequest):
    _parse_uuid(space_id, "space_id")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = api.update_space(space_id, **changes)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/api/spaces/{space_id}")
def delete_space(space_id: str):
    _parse_uuid(space_id, "space_id")
    result = api.delete_space(space_id)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result
