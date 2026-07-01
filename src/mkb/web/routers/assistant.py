from fastapi import APIRouter, HTTPException

from mkb.web._helpers import start_web_job_action
from mkb.web._models import AssistantChatRequest
from mkb.web._state import _dispatch_pending_workflows, _get_assistant_session, jobs

router = APIRouter()


@router.post("/api/assistant/chat")
def assistant_chat(body: AssistantChatRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    session = _get_assistant_session()

    job_id = start_web_job_action(
        jobs,
        "assistant_chat",
        runner=session.runner,
        session_id=session.session_id,
        message=message,
        dispatch_pending_workflows=_dispatch_pending_workflows,
    )
    return {"job_id": job_id}
