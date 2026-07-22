"""
Review agent for multi-turn extraction.

Examines existing knowledge frames against source files to identify
and correct gaps, inconsistencies, and evidence level issues.
"""

from __future__ import annotations

import logging
import uuid

from google.adk.agents import Agent

from mkb.agents._utils import create_llm
from mkb.agents.prompts.review import REVIEW_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.runtime import AgentRuntime
from mkb.agents.tools.frames import frame_tools
from mkb.agents.tools.reading import reading_tools
from mkb.db.models import KnowledgeFrame

logger = logging.getLogger(__name__)

APP_NAME = "mkb_review"


def build_review_agent(runtime: AgentRuntime, model: str | None = None) -> Agent:
    """Create a configured review agent."""
    return Agent(
        name="knowledge_reviewer",
        model=create_llm(model),
        instruction=REVIEW_PROMPT,
        tools=reading_tools(runtime) + frame_tools(runtime),
    )


async def run_review_pass(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    runtime: AgentRuntime | None = None,
) -> dict:
    """Run a single review pass on an existing knowledge frame.

    Returns dict with review results and whether changes were made.
    """
    if runtime is None:
        raise ValueError("Review requires an explicit AgentRuntime")
    agent = build_review_agent(runtime, model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"review_{project_id}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    # Get project label for context
    with runtime.database.session() as db:
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
    with runtime.database.session() as db:
        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        new_version = frame.extraction_version if frame else current_version
        changes_made = new_version > current_version

    return {
        "no_changes": not changes_made,
        "agent_summary": result.final_text,
        "total_events": len(result.events_collected),
    }
