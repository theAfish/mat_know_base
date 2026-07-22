"""
Projection fixer sub-agent — re-examines specific fields against
source material when the projection reviewer needs verification.

This agent does NOT save any data. It reads source material and
returns corrections/findings to the reviewer.
"""

from __future__ import annotations

import logging
import uuid

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, run_async_sync
from mkb.agents.prompts.projection_fixer import PROJECTION_FIXER_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.runtime import AgentRuntime
from mkb.agents.tools.reading import reading_tools

logger = logging.getLogger(__name__)

APP_NAME = "mkb_projection_fixer"


def build_fixer_agent(runtime: AgentRuntime, model: str | None = None) -> Agent:
    """Create a projection fixer agent with reading + frame tools."""
    llm = create_llm(model)
    return Agent(
        name="projection_fixer",
        model=llm,
        instruction=PROJECTION_FIXER_PROMPT,
        tools=reading_tools(runtime),
    )


async def _run_fixer_async(
    project_id: uuid.UUID,
    fields: str,
    context: str = "",
    model: str | None = None,
    verbose: bool = False,
    runtime: AgentRuntime | None = None,
) -> dict:
    """Run the fixer sub-agent to re-examine specific fields."""
    if runtime is None:
        raise ValueError("Projection fixing requires an explicit AgentRuntime")
    agent = build_fixer_agent(runtime, model)
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
    runtime: AgentRuntime | None = None,
) -> dict:
    """Run fixer on specific fields."""
    return run_async_sync(
        _run_fixer_async(
            project_id,
            fields,
            context,
            model,
            verbose,
            runtime=runtime,
        )
    )
