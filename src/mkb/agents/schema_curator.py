"""Evidence-grounded global Schema Curator Agent."""

from __future__ import annotations

import uuid

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, sync_agent_run
from mkb.agents.prompts.schema_curator import SCHEMA_CURATOR_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.schema_curator import (
    SCHEMA_CURATOR_TOOLS, reset_curator_author, set_curator_author,
)
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import SchemaProposal, SchemaProposalRevision

APP_NAME = "mkb_schema_curator"


def build_schema_curator(model: str | None = None) -> Agent:
    return Agent(
        name="schema_curator",
        model=create_llm(model),
        instruction=SCHEMA_CURATOR_PROMPT,
        tools=SCHEMA_CURATOR_TOOLS,
    )


@sync_agent_run
async def run_schema_curator(
    *, min_support: int = 2, author: str = "schema-curator/ui",
    model: str | None = None, verbose: bool = False, progress_callback=None,
) -> dict:
    if model is None:
        from mkb.runtime_settings import get_setting
        model = get_setting("extraction_model")
    attributed_author = f"schema-curator-agent/{model} requested-by/{author}"
    with SyncSessionLocal() as session:
        before = session.query(SchemaProposal).count()
        revisions_before = session.query(SchemaProposalRevision).count()
    token = set_curator_author(attributed_author)
    try:
        runner = AgentRunner(
            agent=build_schema_curator(model), app_name=APP_NAME, max_llm_calls=30,
        )
        session_id = f"schema_curator_{uuid.uuid4()}"
        await runner.create_session(session_id)
        result = await runner.run(
            session_id=session_id,
            message=(
                "Analyze the global workflow corpus and draft evidence-grounded schema "
                f"proposals. Use minimum support {max(1, min_support)}."
            ),
            verbose=verbose,
            progress_callback=progress_callback,
        )
    finally:
        reset_curator_author(token)
    if not result.success:
        return {"status": "error", "message": result.error or "Curator agent failed"}
    with SyncSessionLocal() as session:
        after = session.query(SchemaProposal).count()
        revisions_after = session.query(SchemaProposalRevision).count()
    return {
        "status": "completed", "proposal_count": max(0, after - before),
        "revision_count": max(0, revisions_after - revisions_before),
        "agent": attributed_author,
    }
