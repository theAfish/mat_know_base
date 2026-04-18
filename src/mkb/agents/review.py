"""
Review agent for multi-turn extraction.

Examines existing knowledge frames against source files to identify
and correct gaps, inconsistencies, and evidence level issues.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

from mkb.agents.prompts.review import REVIEW_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools import ALL_TOOLS
from mkb.config import settings
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import KnowledgeFrame

logger = logging.getLogger(__name__)

APP_NAME = "mkb_review"


def build_review_agent(model: str | None = None) -> Agent:
    """Create a configured review agent."""
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.openai_api_base:
        os.environ.setdefault("OPENAI_API_BASE", settings.openai_api_base)

    llm = LiteLlm(model=model or settings.extraction_model)
    return Agent(
        name="knowledge_reviewer",
        model=llm,
        instruction=REVIEW_PROMPT,
        tools=ALL_TOOLS,
    )


async def run_review_pass(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run a single review pass on an existing knowledge frame.

    Returns dict with review results and whether changes were made.
    """
    agent = build_review_agent(model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"review_{project_id}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    # Get project label for context
    with SyncSessionLocal() as db:
        from mkb.db.models import ResearchProject
        project = db.query(ResearchProject).filter_by(project_id=project_id).first()
        project_label = project.label if project else str(project_id)

        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        if not frame or not frame.content:
            return {"no_changes": True, "reason": "No frame content to review"}
        current_version = frame.extraction_version

    message = (
        f"Review the knowledge frame for project {project_id} "
        f"(label: {project_label}). "
        f"Check for completeness, accuracy, consistency, and proper "
        f"evidence levels. Apply any needed corrections using "
        f"update_knowledge_frame."
    )

    result = await runner.run(
        session_id=session_id,
        message=message,
        verbose=verbose,
    )

    if not result.success:
        logger.error("Review pass failed for project %s: %s", project_id, result.error)
        return {"no_changes": True, "error": result.error}

    # Check if changes were actually made
    with SyncSessionLocal() as db:
        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        new_version = frame.extraction_version if frame else current_version
        changes_made = new_version > current_version

    return {
        "no_changes": not changes_made,
        "agent_summary": result.final_text,
        "total_events": len(result.events_collected),
    }
