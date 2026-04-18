"""
Feedback review agent — re-examines knowledge frames based on
feedback from projection agents.

Activated by the user (not automatically by projection agents).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

from mkb.agents.prompts.feedback_review import FEEDBACK_REVIEW_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.reading import READING_TOOLS
from mkb.agents.tools.frames import FRAME_TOOLS
from mkb.agents.tools.feedback import FEEDBACK_TOOLS
from mkb.config import settings
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Feedback, FeedbackStatus

logger = logging.getLogger(__name__)

APP_NAME = "mkb_feedback_review"

# Combine reading + frame + feedback tools for the review agent
FEEDBACK_REVIEW_TOOLS = READING_TOOLS + FRAME_TOOLS + FEEDBACK_TOOLS


def build_feedback_review_agent(model: str | None = None) -> Agent:
    """Create a feedback review agent."""
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.openai_api_base:
        os.environ.setdefault("OPENAI_API_BASE", settings.openai_api_base)

    llm = LiteLlm(model=model or settings.extraction_model)
    return Agent(
        name="feedback_reviewer",
        model=llm,
        instruction=FEEDBACK_REVIEW_PROMPT,
        tools=FEEDBACK_REVIEW_TOOLS,
    )


async def _run_feedback_review_async(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run feedback review on a project's knowledge frame."""

    # Check if there's any open feedback
    with SyncSessionLocal() as db:
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

    agent = build_feedback_review_agent(model)
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
    with SyncSessionLocal() as db:
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
) -> dict:
    """Synchronous wrapper — run feedback review on one project."""
    return asyncio.run(_run_feedback_review_async(project_id, model, verbose))
