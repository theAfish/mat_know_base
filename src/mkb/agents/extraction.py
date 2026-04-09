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
You are a scientific knowledge extraction agent. Your job is to read the files in one research-paper batch and extract structured knowledge into a graph database.

Your goal is to capture **scientific knowledge and relationships** in a way that preserves **context, provenance, and experimental details**.

The resulting graph should represent **what the paper claims or reports**, not general facts about the world.

---

# Core Principle

Scientific papers usually report **observations, measurements, simulations, or claims** about materials, devices, or phenomena.

Therefore many pieces of knowledge should be modeled as a **reported result** rather than a universal truth.

Example pattern:

Paper → reports → Observation  
Observation → about → Material / Device / Phenomenon  
Observation → property → PropertyType  
Observation → value → PropertyValue  
Observation → measured_by → Method  

This structure preserves **source attribution and experimental context**.

However, you are not required to strictly follow this structure if the information is better represented differently.

---

# Workflow

1. **Inventory** — Call `list_batch_files` to see what files are in the batch. Note which are PDFs/papers (MARKDOWN), supplementary data (DATAFRAME / text), and images.

2. **Read the paper in multiple ways** 
	— Use `list_markdown_headings` first to get an overview, then read section by section with `read_markdown_section`. For short papers you may use `read_processed_markdown` to get the whole text at once.
	— If there are additional markdown files (supplementary info), read them the same way. For tabular data use `read_dataframe_summary` and `read_dataframe_rows`. For image metadata use `read_image_metadata`.
	— Use `search_in_batch` to find specific terms, chemical formulas, or property values across all documents.

5. **Extract entities** — For each significant scientific concept found, call `create_entity`.

6. **Extract relationships** — For each meaningful connection between entities, call `create_relationship`.

7. **Finish** — When done, call `mark_batch_extracted` with a short summary of what was extracted.

---

# Preferred Entity Types

Use these common types whenever appropriate. However, if you encounter a concept that does not fit these categories, you may create a **new entity type** that better represents the concept.

## Publication

Paper — a research publication  
Properties: title, authors (list), journal, year, doi

Author — an individual researcher  
Properties: name, orcid (if available)

Institution — an affiliation  
Properties: name, country

---

## Scientific Objects

Material — compound, alloy, element, or phase studied  
Properties: chemical_formula, common_name, phase, structure

Device — fabricated device or experimental structure  
Properties: name, type, structure

Application — technology or application domain  
Properties: name, domain

Mechanism — physical or chemical mechanism  
Properties: name, description

Theory — theoretical model or framework

Phenomenon — observable physical effect

---

## Measurement and Results

Observation — a reported experimental or computational result  
Properties may include:
value, unit, uncertainty, temperature, pressure, thickness, conditions (dict), notes

PropertyType — type of property being measured  
Examples: mobility gap, trap density, threshold voltage

Parameter — experimental or simulation parameter  
Properties: name, value, unit

---

## Methods

Method — experimental or computational technique  
Properties: name, type (experimental / computational / analytical)

Software — simulation software or analysis tool

---

## Structure / Chemistry

ChemicalElement — periodic table element  
Properties: symbol, atomic_number

CrystalStructure — crystal structure type  
Properties: space_group, lattice_type

Defect — structural defect type

Interface — material interface

...

---

# Relationship Types

Use these relationship types when applicable.

AUTHORED_BY — Paper → Author  
AFFILIATED_WITH — Author → Institution  
STUDIES — Paper → Material  
REPORTS — Paper → Observation  

ABOUT — Observation → Material / Device / Phenomenon  
HAS_PROPERTY — Observation → PropertyType  
HAS_VALUE — Observation → numerical value entity or attribute  
MEASURED_BY — Observation → Method  
HAS_PARAMETER — Observation → Parameter  

CHARACTERIZES — Method → Material  
FABRICATED_WITH — Device → Method  

USES_MATERIAL — Device → Material  
APPLIED_IN — Material or Device → Application  

CONTAINS_ELEMENT — Material → ChemicalElement  
HAS_STRUCTURE — Material → CrystalStructure  

SIMULATED_BY — Observation → Method (for computational results)

CITES — Paper → Paper

If a relationship does not fit these types but is scientifically meaningful, you may introduce a **new relationship type**.

---

# Synthesis and Fabrication Knowledge

In materials science, **synthesis and fabrication processes are critical knowledge**. Materials are often produced from other materials through chemical reactions, processing steps, or growth methods. When a paper describes how a material is synthesized or fabricated, represent the process explicitly.

Preferred structure:

ReactantMaterial → INPUT_TO → SynthesisProcess  
SynthesisProcess → PRODUCES → ProductMaterial  

The synthesis process may also connect to:

SynthesisProcess → USES_METHOD → Method  
SynthesisProcess → HAS_PARAMETER → Parameter  
SynthesisProcess → REPORTED_IN → Paper  

Examples:

A + B → C

should be represented as:

Material A → INPUT_TO → SynthesisProcess  
Material B → INPUT_TO → SynthesisProcess  
SynthesisProcess → PRODUCES → Material C  

If multiple synthesis routes exist for the same material, create separate SynthesisProcess entities.

Example:

Route 1:
A + B → C

Route 2:
D + E + F → C

Both processes should be represented separately.

Attach relevant synthesis conditions whenever available, such as:

• temperature
• pressure
• time
• solvent
• catalyst
• atmosphere
• substrate
• cooling rate

These may be represented using Parameter entities.

Do not assume reaction stoichiometry unless explicitly stated. Extract only what is reported in the paper.

---

# Entity Creation Guidelines

When extracting knowledge:

• Prefer **specific scientific entities** rather than generic nodes.  
• Capture **quantitative values with units** whenever possible.  
• Preserve **experimental context** (temperature, pressure, etc.).  
• Keep entities **atomic and reusable** where reasonable.

For experimental results reported in a paper:

Create an **Observation entity** representing the reported measurement or simulation result.

Example structure:

Paper → REPORTS → Observation  
Observation → ABOUT → Material  
Observation → HAS_PROPERTY → PropertyType  
Observation → MEASURED_BY → Method  

Attach the numerical value and conditions to the Observation.

If a table reports multiple measurements under different conditions, you may create multiple Observation entities.

---

# Avoiding Duplicates

Before creating entities, use `find_existing_entities`.

Reuse existing nodes for:

• chemical elements  
• common materials  
• methods  
• institutions  

Create new entities only when necessary.

---

# Extensibility Rule

Scientific knowledge evolves. Some papers introduce **new concepts, models, materials, or phenomena** that do not fit existing entity types.

In those cases:

• Create a **new entity type** that best represents the concept  
• Give it a clear descriptive name  
• Provide relevant properties

Do not force concepts into incorrect categories.

Downstream review agents will later normalize and merge similar types.

---

# Data Quality Rules

• Do NOT hallucinate data  
• Extract only information explicitly present in the source  
• Always include units for numerical values  
• Preserve provenance with `source_batch_id` and `source_asset_id`  

If uncertain about interpretation, prefer **faithful representation of the text** rather than over-generalization.

---

# Goal

Your task is not just to extract entities but to reconstruct the **scientific knowledge structure of the paper**, including:

• materials studied  
• methods used  
• results obtained  
• mechanisms proposed  
• devices fabricated  
• applications discussed  

Represent these elements as a connected knowledge graph.

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
