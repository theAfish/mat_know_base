"""Corpus-level Ontology Induction Agent.

This is the second agent in the workflow architecture. ``schema_curator`` is
retained as a compatibility import for existing API clients.
"""

from __future__ import annotations

import uuid

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, sync_agent_run
from mkb.agents.prompts.ontology_induction import ONTOLOGY_INDUCTION_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.schema_curator import (
    SCHEMA_CURATOR_TOOLS, reset_curator_author, set_curator_author,
)
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import SchemaProposal, SchemaProposalRevision

APP_NAME = "mkb_ontology_induction"


def build_ontology_induction_agent(model: str | None = None) -> Agent:
    return Agent(
        name="ontology_induction_agent",
        model=create_llm(model),
        instruction=ONTOLOGY_INDUCTION_PROMPT,
        tools=SCHEMA_CURATOR_TOOLS,
    )


@sync_agent_run
async def run_ontology_induction(
    *, min_support: int = 2, author: str = "ontology-induction/ui",
    model: str | None = None, verbose: bool = False, progress_callback=None,
) -> dict:
    if model is None:
        from mkb.runtime_settings import get_setting
        model = get_setting("extraction_model")
    attributed_author = f"ontology-induction-agent/{model} requested-by/{author}"
    with SyncSessionLocal() as session:
        before = session.query(SchemaProposal).count()
        revisions_before = session.query(SchemaProposalRevision).count()
    token = set_curator_author(attributed_author)
    try:
        runner = AgentRunner(
            agent=build_ontology_induction_agent(model),
            app_name=APP_NAME,
            max_llm_calls=30,
        )
        session_id = f"ontology_induction_{uuid.uuid4()}"
        await runner.create_session(session_id)
        result = await runner.run(
            session_id=session_id,
            message=(
                "Analyze the global card-based workflow corpus and draft "
                "evidence-grounded ontology proposals with minimum support "
                f"{max(1, min_support)}."
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
        "proposal_count": max(0, after - before),
        "revision_count": max(0, revisions_after - revisions_before),
        "agent": attributed_author,
    }
