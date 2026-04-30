"""
MKB Orchestrator Agent.

A conversational agent that helps users manage their knowledge base:
- Inspect project status, frames, projections, and feedback
- Queue workflow jobs (extraction, projection, KG, reviews)
- Answer questions about the system state

Session persistence: the AgentRunner instance is kept alive between chat
messages (stored in Streamlit session state) so conversation history is
preserved across Streamlit reruns.
"""

from __future__ import annotations

import logging
import uuid

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, run_async_sync
from mkb.agents.prompts.orchestrator import ORCHESTRATOR_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.orchestrator_tools import ORCHESTRATOR_TOOLS, get_pending_workflows  # noqa: F401 — re-exported

logger = logging.getLogger(__name__)

APP_NAME = "mkb_orchestrator"


def build_orchestrator_agent(model: str | None = None) -> Agent:
    """Create the orchestrator agent with all status + action tools."""
    llm = create_llm(model)
    return Agent(
        name="mkb_orchestrator",
        model=llm,
        instruction=ORCHESTRATOR_PROMPT,
        tools=ORCHESTRATOR_TOOLS,
    )


def create_orchestrator_runner(model: str | None = None) -> tuple[AgentRunner, str]:
    """Create an AgentRunner + session for a new orchestrator conversation.

    Returns:
        (runner, session_id) — store both in Streamlit session state to
        preserve conversation history across reruns.
    """
    agent = build_orchestrator_agent(model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)
    session_id = str(uuid.uuid4())
    run_async_sync(runner.create_session(session_id=session_id, user_id="ui_user"))
    return runner, session_id


def send_message(
    runner: AgentRunner,
    session_id: str,
    message: str,
    progress_callback=None,
) -> dict:
    """Send a user message to the orchestrator and return the reply.

    Designed to be called from a start_job background thread.

    Returns:
        {"reply": str, "success": bool, "error": str | None}
    """
    result = run_async_sync(
        runner.run(
            session_id=session_id,
            message=message,
            user_id="ui_user",
            progress_callback=progress_callback,
        )
    )
    return {
        "reply": result.final_text,
        "success": result.success,
        "error": result.error,
    }
