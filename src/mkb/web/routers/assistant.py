from fastapi import APIRouter, HTTPException

from mkb.web.dependencies import get_knowledge_base
from mkb.web._models import AssistantChatRequest

router = APIRouter()


@router.post("/api/assistant/chat")
def assistant_chat(body: AssistantChatRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    job = get_knowledge_base().assistant.chat(message)
    return {"job_id": str(job.id)}
