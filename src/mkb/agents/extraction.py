"""
Knowledge-extraction agent built on google-adk.

Provides:
- EXTRACTION_PROMPT: the system instruction that guides the LLM
- build_extraction_agent(): factory that wires tools + prompt into an Agent
- run_extraction(): high-level function that runs extraction on one batch
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from mkb.agents.tools import ALL_TOOLS
from mkb.config import settings
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import ExtractionStatus, IngestionBatch

logger = logging.getLogger(__name__)

# =====================================================================
# System prompt
# =====================================================================

EXTRACTION_PROMPT = """\
You are a scientific knowledge extraction agent.  Your job is to read
the files in one research-paper batch and extract structured knowledge
into a graph database.

## Workflow

1. **Inventory** — Call `list_batch_files` to see what files are in the
   batch.  Note which are PDFs/papers (MARKDOWN), supplementary data
   (DATAFRAME / text), and images.

2. **Read the main paper** — Use `list_markdown_headings` first to get an
   overview, then read section by section with `read_markdown_section`.
   For short papers you may use `read_processed_markdown` to get the
   whole text at once.

3. **Read supplementary material** — If there are additional markdown
   files (supplementary info), read them the same way.  For tabular data
   use `read_dataframe_summary` and `read_dataframe_rows`.  For image
   metadata use `read_image_metadata`.

4. **Search when needed** — Use `search_in_batch` to find specific terms,
   chemical formulas, or property values across all documents.

5. **Extract entities** — For each significant scientific concept found,
   call `create_entity`.  Follow the entity type conventions below.

6. **Extract relationships** — For each meaningful connection between
   entities, call `create_relationship`.

7. **Finish** — When done, call `mark_batch_extracted` with a short
   summary of what was extracted.

## Entity Types (use exactly these strings)

- **Paper** — The publication itself.
  Properties: title, authors (list), journal, year, doi.
- **Author** — An individual author.
  Properties: name, orcid (if available).
- **Institution** — An affiliation.
  Properties: name, country.
- **Material** — A material, compound, or alloy studied.
  Properties: chemical_formula, common_name, phase, structure.
- **Property** — A physical/chemical property that was measured or
  computed.  Properties: name, value, unit, conditions (dict of temp,
  pressure, etc.), measurement_method.
- **Method** — An experimental or computational technique.
  Properties: name, type (experimental / computational / analytical).
- **Device** — A fabricated device or test structure.
  Properties: name, type, dimensions.
- **Parameter** — A key experimental/simulation parameter.
  Properties: name, value, unit.
- **ChemicalElement** — A chemical element.
  Properties: symbol, atomic_number.
- **CrystalStructure** — A crystal structure type.
  Properties: space_group, lattice_type.
- **Application** — A technology or application area.
  Properties: name, domain.

## Relationship Types (use exactly these strings)

- AUTHORED_BY — Paper → Author
- AFFILIATED_WITH — Author → Institution
- STUDIES — Paper → Material
- HAS_PROPERTY — Material → Property
- MEASURED_BY — Property → Method
- EXHIBITS — Material → Property (for intrinsic properties)
- FABRICATED_WITH — Device → Method
- CONTAINS_ELEMENT — Material → ChemicalElement
- HAS_STRUCTURE — Material → CrystalStructure
- SIMULATED_BY — Property → Method (computational)
- APPLIED_IN — Material → Application
- CITES — Paper → Paper (if you find referenced work with enough info)
- CHARACTERIZES — Method → Material
- HAS_PARAMETER — Method → Parameter

## Rules

- Always set `source_batch_id` when creating entities so they are
  traceable.
- Set `source_asset_id` to the specific file where you found the
  information.
- Use `find_existing_entities` before creating an entity to avoid
  duplicates — especially for common things like chemical elements.
- For numerical properties, always include the **unit** and
  **conditions** in the properties dict.
- Prefer specific entity types over generic ones.
- Extract **quantitative** data whenever possible (numeric values with
  units), not just qualitative statements.
- If a paper reports multiple measurements of the same property under
  different conditions, create separate Property entities for each.
- Do NOT hallucinate data.  Only extract what is explicitly stated in
  the source documents.
"""

APP_NAME = "mkb_extraction"


# =====================================================================
# Agent factory
# =====================================================================


def build_extraction_agent(model: str | None = None) -> Agent:
    """Create a configured extraction agent with all tools."""
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.openai_api_base:
        os.environ.setdefault("OPENAI_API_BASE", settings.openai_api_base)

    llm = LiteLlm(model=model or settings.extraction_model)
    return Agent(
        name="knowledge_extractor",
        model=llm,
        instruction=EXTRACTION_PROMPT,
        tools=ALL_TOOLS,
    )


# =====================================================================
# Runner
# =====================================================================


async def _run_extraction_async(
    batch_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Core async extraction loop for one batch."""

    agent = build_extraction_agent(model)
    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    user_id = "mkb_system"
    session_id = f"extract_{batch_id}"

    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )

    # Mark batch as in-progress
    with SyncSessionLocal() as db:
        batch = db.query(IngestionBatch).filter_by(batch_id=batch_id).first()
        if not batch:
            return {"status": "error", "message": f"Batch {batch_id} not found"}
        batch.extraction_status = ExtractionStatus.IN_PROGRESS
        db.commit()
        batch_label = batch.label or str(batch_id)

    # Initial message tells the agent which batch to work on
    initial_message = genai_types.Content(
        role="user",
        parts=[
            genai_types.Part(
                text=(
                    f"Extract knowledge from batch {batch_id} "
                    f"(label: {batch_label}).  "
                    f"Start by listing the files, then systematically "
                    f"read and extract all scientific knowledge."
                )
            )
        ],
    )

    events_collected = []
    final_text = ""

    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=initial_message,
        ):
            if verbose and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        logger.info("Agent: %s", part.text[:200])
                    if part.function_call:
                        logger.info(
                            "Tool call: %s(%s)",
                            part.function_call.name,
                            list(part.function_call.args.keys()) if part.function_call.args else [],
                        )

            events_collected.append(event)

            if event.is_final_response() and event.content and event.content.parts:
                final_text = "\n".join(
                    p.text for p in event.content.parts if p.text
                )

    except Exception as exc:
        logger.error("Extraction failed for batch %s: %s", batch_id, exc)
        with SyncSessionLocal() as db:
            batch = db.query(IngestionBatch).filter_by(batch_id=batch_id).first()
            if batch:
                batch.extraction_status = ExtractionStatus.FAILED
                meta = dict(batch.extraction_metadata or {})
                meta["error"] = str(exc)
                meta["failed_at"] = datetime.now(timezone.utc).isoformat()
                batch.extraction_metadata = meta
                db.commit()
        return {
            "status": "error",
            "batch_id": str(batch_id),
            "message": str(exc),
        }

    # Count entities and relationships created
    with SyncSessionLocal() as db:
        from mkb.db.models import KnowledgeEdge, KnowledgeNode

        node_count = (
            db.query(KnowledgeNode)
            .filter_by(source_batch_id=batch_id)
            .count()
        )
        edge_count = 0
        node_ids = [
            n.node_id
            for n in db.query(KnowledgeNode.node_id)
            .filter_by(source_batch_id=batch_id)
            .all()
        ]
        if node_ids:
            edge_count = (
                db.query(KnowledgeEdge)
                .filter(KnowledgeEdge.source_node_id.in_(node_ids))
                .count()
            )

    return {
        "status": "completed",
        "batch_id": str(batch_id),
        "entities_created": node_count,
        "relationships_created": edge_count,
        "agent_summary": final_text,
        "total_events": len(events_collected),
    }


def run_extraction(
    batch_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Synchronous wrapper — run extraction on one batch.

    Returns a summary dict with status, entity/edge counts, etc.
    """
    return asyncio.run(_run_extraction_async(batch_id, model, verbose))


def run_extraction_all(
    limit: int | None = None,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run extraction on all batches that haven't been extracted yet.

    Returns an aggregate summary.
    """
    with SyncSessionLocal() as db:
        q = db.query(IngestionBatch).filter(
            IngestionBatch.extraction_status.in_([
                ExtractionStatus.PENDING,
                ExtractionStatus.FAILED,
            ])
        )
        if limit:
            q = q.limit(limit)
        batches = q.all()
        batch_ids = [b.batch_id for b in batches]

    results = []
    for bid in batch_ids:
        logger.info("Extracting batch %s ...", bid)
        result = run_extraction(bid, model=model, verbose=verbose)
        results.append(result)
        status = result.get("status", "unknown")
        entities = result.get("entities_created", 0)
        edges = result.get("relationships_created", 0)
        logger.info(
            "  → %s  (%d entities, %d relationships)", status, entities, edges
        )

    return {
        "total_batches": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "error"),
        "total_entities": sum(r.get("entities_created", 0) for r in results),
        "total_relationships": sum(
            r.get("relationships_created", 0) for r in results
        ),
        "results": results,
    }
