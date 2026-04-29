"""Knowledge graph review agent.

Two modes:
- global: analyzes the full graph for duplicate concepts and inconsistent relation names
- local:  explores the neighborhood of least-reviewed concepts and fixes local issues
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import Literal

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, sync_agent_run
from mkb.agents.prompts.graph_review import GRAPH_REVIEW_GLOBAL_PROMPT, GRAPH_REVIEW_LOCAL_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.graph_review import (
    GRAPH_REVIEW_COMMON_TOOLS,
    GRAPH_REVIEW_LOCAL_TOOLS,
    _flush_review_session_to_db,
    _get_least_examined_concepts,
    _init_review_session,
    _progress_cb,
)
from mkb.knowledge_graph import ensure_global_kg_space

logger = logging.getLogger(__name__)

APP_NAME = "mkb_graph_review"


def build_graph_review_agent(mode: Literal["global", "local"], model: str | None = None) -> Agent:
    """Create the graph review agent for the given mode."""
    llm = create_llm(model)
    tools = GRAPH_REVIEW_LOCAL_TOOLS if mode == "local" else GRAPH_REVIEW_COMMON_TOOLS
    prompt = GRAPH_REVIEW_LOCAL_PROMPT if mode == "local" else GRAPH_REVIEW_GLOBAL_PROMPT
    return Agent(
        name=f"graph_review_agent_{mode}",
        model=llm,
        instruction=prompt,
        tools=tools,
    )


async def _run_graph_review_async(
    mode: Literal["global", "local"],
    model: str | None = None,
    verbose: bool = False,
    seed_count: int = 10,
    progress_callback=None,
) -> dict:
    """Run one graph review session in the given mode."""
    global_space = ensure_global_kg_space()
    space_id_str = str(global_space.space_id)

    review_session = _init_review_session()

    if progress_callback is not None:
        _progress_cb.set(progress_callback)

    if mode == "local":
        seed_labels = _get_least_examined_concepts(global_space.space_id, seed_count)
        if not seed_labels:
            return {
                "mode": mode,
                "status": "skipped",
                "message": "No concepts found in the knowledge graph.",
                "reviewed_elements": {"examined": 0, "modified": 0},
            }
        seeds_block = "\n".join(f"- {label}" for label in seed_labels)
        message = (
            f"Review the knowledge graph in local mode.\n"
            f"Space ID: {space_id_str}\n\n"
            f"Your starting concepts ({len(seed_labels)}):\n{seeds_block}\n\n"
            "For each starting concept, explore its neighborhood, check for duplicates "
            "and unclear labels, verify against source frames if needed, and fix any issues."
        )
    else:
        message = (
            f"Review the knowledge graph in global mode.\n"
            f"Space ID: {space_id_str}\n\n"
            "Systematically analyze the distribution of relation names, identify synonym clusters, "
            "standardize relation naming, and merge duplicate or synonymous concept nodes."
        )

    agent = build_graph_review_agent(mode, model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)
    session_id = f"graph_review_{mode}_{uuid.uuid4()}"
    await runner.create_session(session_id)

    result = await runner.run(session_id=session_id, message=message, verbose=verbose)

    stats = _flush_review_session_to_db(global_space.space_id, review_session)

    return {
        "mode": mode,
        "status": "completed" if result.success else "error",
        "space_id": space_id_str,
        "agent_summary": result.final_text,
        "reviewed_elements": stats,
        **({"seed_concepts": seed_labels} if mode == "local" else {}),
        **({"error": result.error} if not result.success else {}),
    }


@sync_agent_run
async def run_graph_review(
    mode: str = "auto",
    model: str | None = None,
    verbose: bool = False,
    seed_count: int = 10,
    progress_callback=None,
) -> dict:
    """Run a knowledge graph review session.

    Args:
        mode: "global", "local", or "auto" (randomly picks global or local).
        model: LLM model override.
        verbose: Enable verbose logging.
        seed_count: Number of starting concepts for local mode.
        progress_callback: Optional callable invoked on each tool call with a progress dict.
    """
    resolved_mode: Literal["global", "local"]
    if mode == "auto":
        resolved_mode = random.choice(["global", "local"])
        logger.info("Graph review mode auto-selected: %s", resolved_mode)
    elif mode in ("global", "local"):
        resolved_mode = mode  # type: ignore[assignment]
    else:
        return {"status": "error", "message": f"Unknown mode '{mode}'. Use 'global', 'local', or 'auto'."}

    return await _run_graph_review_async(resolved_mode, model, verbose, seed_count, progress_callback)
