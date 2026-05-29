from fastapi import APIRouter, HTTPException

from mkb.agents.orchestrator import send_message
from mkb.web._models import AssistantChatRequest
from mkb.web._state import _dispatch_pending_workflows, _get_assistant_session, jobs

router = APIRouter()


@router.post("/api/assistant/chat")
def assistant_chat(body: AssistantChatRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    session = _get_assistant_session()

    def _run_chat(progress_callback=None):
        result = send_message(
            runner=session.runner,
            session_id=session.session_id,
            message=message,
            progress_callback=progress_callback,
        )
        _dispatch_pending_workflows()
        return result

    job_id = jobs.start_job(
        kind="orchestrator_chat",
        label="Assistant",
        target=_run_chat,
    )
    return {"job_id": job_id}
