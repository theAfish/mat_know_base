"""
Feedback review agent — re-examines knowledge frames based on
feedback from projection agents.

Activated by the user (not automatically by projection agents).
"""

from __future__ import annotations

import logging
import uuid

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, run_async_sync
from mkb.agents.prompts.feedback_review import FEEDBACK_REVIEW_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.runtime import AgentRuntime
from mkb.agents.tools.feedback import feedback_tools
from mkb.agents.tools.frames import frame_tools
from mkb.agents.tools.reading import reading_tools
from mkb.db.models import Feedback, FeedbackStatus

logger = logging.getLogger(__name__)

APP_NAME = "mkb_feedback_review"

def build_feedback_review_agent(
    runtime: AgentRuntime,
    model: str | None = None,
) -> Agent:
    """Create a feedback review agent."""
    return Agent(
        name="feedback_reviewer",
        model=create_llm(model),
        instruction=FEEDBACK_REVIEW_PROMPT,
        tools=reading_tools(runtime) + frame_tools(runtime) + feedback_tools(runtime),
    )


async def _run_feedback_review_async(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    runtime: AgentRuntime | None = None,
) -> dict:
    """Run feedback review on a project's knowledge frame."""

    # Check if there's any open feedback
    if runtime is None:
        raise ValueError("Feedback review requires an explicit AgentRuntime")
    with runtime.database.session() as db:
        open_count = (
            db.query(Feedback)
            .filter_by(target_project_id=project_id, status=FeedbackStatus.OPEN)
            .count()
        )
        if open_count == 0:
            return {
                "status": "no_feedback",
                "project_id": str(project_id),
                "message": "No open feedback items for this project.",
            }

    agent = build_feedback_review_agent(runtime, model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"feedback_review_{project_id}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    message = (
        f"Review and resolve open feedback for project {project_id}. "
        f"There are {open_count} open feedback items. "
        f"Start by getting the pending feedback, then review each item "
        f"against the source material."
    )

    result = await runner.run(
        session_id=session_id,
        message=message,
        verbose=verbose,
    )

    # Count resolutions
    with runtime.database.session() as db:
        remaining = (
            db.query(Feedback)
            .filter_by(target_project_id=project_id, status=FeedbackStatus.OPEN)
            .count()
        )

    return {
        "status": "completed" if result.success else "error",
        "project_id": str(project_id),
        "initial_open": open_count,
        "remaining_open": remaining,
        "resolved": open_count - remaining,
        "agent_summary": result.final_text,
        "error": result.error if not result.success else None,
    }


def run_feedback_review(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    runtime: AgentRuntime | None = None,
) -> dict:
    """Synchronous wrapper — run feedback review on one project."""
    return run_async_sync(
        _run_feedback_review_async(project_id, model, verbose, runtime=runtime)
    )
