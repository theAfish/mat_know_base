"""
Projection fixer sub-agent — re-examines specific fields against
source material when the projection reviewer needs verification.

This agent does NOT save any data. It reads source material and
returns corrections/findings to the reviewer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

from mkb.agents.prompts.projection_fixer import PROJECTION_FIXER_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools import ALL_TOOLS
from mkb.config import settings

logger = logging.getLogger(__name__)

APP_NAME = "mkb_projection_fixer"


def build_fixer_agent(model: str | None = None) -> Agent:
    """Create a projection fixer agent with reading + frame tools."""
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.openai_api_base:
        os.environ.setdefault("OPENAI_API_BASE", settings.openai_api_base)

    llm = LiteLlm(model=model or settings.extraction_model)
    return Agent(
        name="projection_fixer",
        model=llm,
        instruction=PROJECTION_FIXER_PROMPT,
        tools=ALL_TOOLS,  # READING_TOOLS + FRAME_TOOLS
    )


async def _run_fixer_async(
    project_id: uuid.UUID,
    fields: str,
    context: str = "",
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run the fixer sub-agent to re-examine specific fields."""
    agent = build_fixer_agent(model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"fixer_{project_id}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    message = (
        f"Re-examine the following fields for project {project_id}:\n\n"
        f"Fields to check:\n{fields}\n"
    )
    if context:
        message += f"\nReviewer context:\n{context}\n"

    message += (
        "\nStart by listing the project files, then read the relevant "
        "source sections to verify these data points. Report your findings."
    )

    result = await runner.run(
        session_id=session_id,
        message=message,
        verbose=verbose,
    )

    if not result.success:
        return {
            "status": "error",
            "error": result.error,
            "fields": fields,
        }

    return {
        "status": "completed",
        "findings": result.final_text,
        "fields": fields,
    }


def run_fixer(
    project_id: uuid.UUID,
    fields: str,
    context: str = "",
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Synchronous wrapper — run fixer on specific fields."""
    return asyncio.run(
        _run_fixer_async(project_id, fields, context, model, verbose)
    )
