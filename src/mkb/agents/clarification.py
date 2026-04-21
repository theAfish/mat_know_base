"""
Frame clarification agent — a lightweight sub-agent invoked inline by the
projection agent to refine a knowledge frame in response to a specific
question.

Unlike the full extraction agent (which processes an entire project from
scratch), this agent performs targeted updates: it reads only the relevant
source sections and applies minimal, surgical changes via
``update_knowledge_frame``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from google.adk.agents import Agent

from mkb.agents._utils import create_llm
from mkb.agents.prompts.frame_clarification import build_clarification_prompt
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.frames import FRAME_TOOLS
from mkb.agents.tools.reading import READING_TOOLS
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import KnowledgeFrame

logger = logging.getLogger(__name__)

APP_NAME = "mkb_clarification"

# The clarification agent needs reading tools (to inspect source files) and
# frame tools (to apply targeted updates).  It intentionally does NOT get
# projection tools — it only modifies the knowledge frame.
CLARIFICATION_TOOLS = READING_TOOLS + FRAME_TOOLS


def build_clarification_agent(
    question: str,
    context: str = "",
    field: str = "",
    model: str | None = None,
) -> Agent:
    """Create a clarification agent for a single targeted question."""
    prompt = build_clarification_prompt(question=question, context=context, field=field)
    llm = create_llm(model)
    return Agent(
        name="clarification_agent",
        model=llm,
        instruction=prompt,
        tools=CLARIFICATION_TOOLS,
    )


async def run_clarification_async(
    project_id: uuid.UUID,
    frame_id: uuid.UUID,
    question: str,
    context: str = "",
    field: str = "",
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run the clarification agent and return the result.

    This coroutine is meant to be executed in a *separate* thread (via
    ``ThreadPoolExecutor``) so it gets its own event loop and does not
    interfere with the caller's async context.
    """
    agent = build_clarification_agent(question=question, context=context, field=field, model=model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"clarify_{frame_id}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    message = (
        f"Clarify the knowledge frame for project {project_id}.\n"
        f"Frame ID: {frame_id}\n"
        f"Question: {question}"
    )
    if field:
        message += f"\nField/section: {field}"
    if context:
        message += f"\nContext from frame:\n{context}"

    result = await runner.run(session_id=session_id, message=message, verbose=verbose)

    if not result.success:
        return {
            "updated": False,
            "error": result.error,
            "clarification_summary": None,
            "updated_frame_content": None,
        }

    # Read back the (potentially updated) frame to return its current content.
    with SyncSessionLocal() as db:
        frame = db.query(KnowledgeFrame).filter_by(frame_id=frame_id).first()
        updated_content = dict(frame.content) if frame and frame.content else {}

    return {
        "updated": True,
        "clarification_summary": result.final_text,
        "updated_frame_content": updated_content,
    }


def run_clarification_in_thread(
    project_id: uuid.UUID,
    frame_id: uuid.UUID,
    question: str,
    context: str = "",
    field: str = "",
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run the clarification agent synchronously in a dedicated thread.

    Using a thread with ``asyncio.run`` avoids the "event loop already running"
    error that would occur when calling an async function from within another
    agent's tool execution (which itself runs inside a running event loop).
    """
    import concurrent.futures

    coro = run_clarification_async(
        project_id=project_id,
        frame_id=frame_id,
        question=question,
        context=context,
        field=field,
        model=model,
        verbose=verbose,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result(timeout=300)
