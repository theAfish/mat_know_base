"""Corpus-level Ontology Induction Agent.

This is the second agent in the workflow architecture. ``schema_curator`` is
retained as a compatibility import for existing API clients.
"""

from __future__ import annotations

import random
import uuid
from typing import Literal

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, sync_agent_run
from mkb.agents.prompts.ontology_induction import (
    WORKFLOW_REVIEW_GLOBAL_PROMPT,
    WORKFLOW_REVIEW_LOCAL_PROMPT,
)
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.schema_curator import (
    SCHEMA_CURATOR_TOOLS, reset_curator_author, set_curator_author,
)
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import SchemaProposal, SchemaProposalRevision

APP_NAME = "mkb_ontology_induction"


def build_ontology_induction_agent(
    mode: Literal["global", "local"],
    model: str | None = None,
) -> Agent:
    return Agent(
        name=f"workflow_review_agent_{mode}",
        model=create_llm(model),
        instruction=(
            WORKFLOW_REVIEW_LOCAL_PROMPT
            if mode == "local"
            else WORKFLOW_REVIEW_GLOBAL_PROMPT
        ),
        tools=SCHEMA_CURATOR_TOOLS,
    )


@sync_agent_run
async def run_ontology_induction(
    *,
    min_support: int = 2,
    author: str = "workflow-review/ui",
    mode: str = "global",
    sample_size: int = 8,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
) -> dict:
    if model is None:
        from mkb.runtime_settings import get_setting
        model = get_setting("extraction_model")
    if mode == "auto":
        resolved_mode: Literal["global", "local"] = random.choice(["global", "local"])
    elif mode in {"global", "local"}:
        resolved_mode = mode  # type: ignore[assignment]
    else:
        return {"status": "error", "message": f"Unknown review mode '{mode}'"}
    attributed_author = f"workflow-review-agent/{resolved_mode}/{model} requested-by/{author}"
    with SyncSessionLocal() as session:
        before = session.query(SchemaProposal).count()
        revisions_before = session.query(SchemaProposalRevision).count()
    token = set_curator_author(attributed_author)
    try:
        runner = AgentRunner(
            agent=build_ontology_induction_agent(resolved_mode, model),
            app_name=APP_NAME,
            max_llm_calls=30,
        )
        session_id = f"ontology_induction_{uuid.uuid4()}"
        await runner.create_session(session_id)
        result = await runner.run(
            session_id=session_id,
            message=(
                f"Run the workflow review agent in {resolved_mode} mode. "
                f"Use minimum support {max(1, min_support)}. "
                f"Use local sample size {max(1, sample_size)} when relevant. "
                "Search newest workflows and the newest card base before making "
                "workflow edits or schema/card-base proposals."
            ),
            verbose=verbose,
            progress_callback=progress_callback,
        )
    finally:
        reset_curator_author(token)
    if not result.success:
        return {"status": "error", "message": result.error or "Ontology induction failed"}
    with SyncSessionLocal() as session:
        after = session.query(SchemaProposal).count()
        revisions_after = session.query(SchemaProposalRevision).count()
    return {
        "status": "completed",
        "mode": resolved_mode,
        "proposal_count": max(0, after - before),
        "revision_count": max(0, revisions_after - revisions_before),
        "agent": attributed_author,
    }
