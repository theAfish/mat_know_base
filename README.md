# mat-know-base

A self-hosted system for ingesting scientific papers and related data into a structured knowledge base. Files are stored immutably using content-addressable storage (SHA256 deduplication), with metadata tracked in PostgreSQL + pgvector and raw binaries in MinIO (S3-compatible). A processing pipeline converts raw files into LLM-readable formats. An LLM agent then extracts structured **knowledge frames** — one per research project — with flexible, agent-decided structure capturing all scientific knowledge from the source material.

## Architecture

```
data/papers/smith2024/     Research package (paper + supplementary)
        │
        ▼
┌──────────────┐  SHA256   ┌─────────────────────────────────┐
│  Ingestion   ├──────────►│  MinIO (S3)                     │
│  Worker      │           │  raw/       ← original files    │
└──────┬───────┘           │  processed/ ← converted outputs │
       │ metadata          └─────────────────────────────────┘
       ▼                           ▲
┌─────────────────┐                │ upload converted files
│  PostgreSQL     │       ┌────────┴──────────┐
│  + pgvector     │       │ Processing Pipeline│
│                 │  ◄────┤  PDF  → .md        │
│  assets         │       │  DOCX → .md        │
│  processed_assets│      │  CSV  → .parquet   │
│  project_assets │       │  IMG  → .json      │
│                 │       └───────────────────┘
│  knowledge_frames│
│  extraction_passes│ ◄── LLM extraction agent (multi-pass)
│  spaces          │
│  projections     │ ◄── Projection agent (space-specific)
│  feedbacks       │ ◄── Feedback loop between agents
└─────────────────┘
```

### Data Flow

1. **Ingest** — Raw files are SHA256-deduplicated, uploaded to MinIO, registered in PostgreSQL as a project
2. **Process** — Raw files are converted to LLM-readable formats (Markdown, Parquet, JSON metadata)
3. **Extract** — An LLM agent reads processed data and produces one **knowledge frame** per project (with optional multi-pass review)
4. **Project** — Domain-specific "spaces" define structured extraction schemas; projection agents extract targeted data from knowledge frames
5. **Feedback** — Projection agents flag unclear data; KB agents review and resolve feedback on user activation

### Knowledge Frame

Each research project produces one knowledge frame with:

- **Paper metadata** (fixed) — title, authors, journal, year, DOI
- **Domain** (fixed) — research domain string
- **Free-form sections** (agent-decided) — the agent chooses what categories best represent the paper's knowledge (e.g., materials, experimental_data, synthesis_routes, mechanisms, etc.)

Every extracted item is tagged with an **evidence level**:
- **Level 1**: Causal experimental evidence
- **Level 2**: Direct experimental observation
- **Level 3**: Correlative evidence
- **Level 4**: Predicted / inferred

### Spaces & Projections

A **Space** defines a domain-specific extraction schema (e.g., "biomineralization templates"). A **Projection** is the result of applying a Space to a knowledge frame — extracting structured data per the schema definition.

### Agentic Feedback

Projection agents can flag ambiguous or missing data. The KB extraction agent can review these feedback items (on user activation) and update the knowledge frame accordingly.

### Projection Review (Multi-Agent)

A strict **Projection Reviewer** agent consolidates and corrects projection data through a multi-agent review process:

```
User activates review
        │
        ▼
┌─────────────────────────┐
│  Projection Reviewer    │  reads all projections + frame + source
│  (strict data auditor)  │
└────────┬────────────────┘
         │ delegates verification
         ▼
┌─────────────────────────┐
│  Projection Fixer       │  re-reads source material
│  (sub-agent)            │  returns corrections
└─────────────────────────┘
         │
         ▼
   Single reviewed projection
   (consolidated, corrected)
```

The reviewer:
1. Loads all projection runs (from single or multiple extraction events)
2. Cross-references against the knowledge frame and original source files
3. Delegates complex verification to the fixer sub-agent
4. Produces a single **reviewed projection** — consolidated, corrected, deduplicated

## Prerequisites

- Python 3.10+
- Docker & Docker Compose
- `libmagic` (usually pre-installed on Linux; `brew install libmagic` on macOS)

## Quick Start

```bash
# 1. Create virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,processing]"

# 2. Copy environment config
cp .env.example .env

# 3. Start infrastructure (PostgreSQL + MinIO)
make up

# 4. Create database tables
python -m mkb setup
```

## Python API (Primary Interface)

The recommended interface is `mkb.api`. See `examples/basic_usage.py` for a complete walkthrough.

```python
from mkb import api

# Setup
api.setup()

# Ingest & process
result = api.ingest("./data/papers/smith2024", label="Smith 2024")
api.process()

# Extract with multi-pass review
api.extract(max_passes=2, verbose=True)

# Query frames
frame = api.get_frame(project_id="...")
print(frame["content"]["paper"])
print(frame["content"].keys())  # agent-decided sections

# Spaces & projections
api.create_space(
    name="biomineralization",
    domain="biomineralization",
    extraction_schema={...},
    system_prompt="...",
    field_descriptions={...},
)
api.project(space_id="...", project_id="...")

# Feedback
api.list_feedback(project_id="...", status="OPEN")
api.review_feedback(project_id="...")

# Projection review (multi-agent)
api.review_projections(space_id="...", project_id="...")
api.review_projections_all(space_id="...")
api.list_reviewed_projections(space_id="...")
api.get_reviewed_projection(reviewed_projection_id="...")

# Streamlit UI
# python -m mkb ui
```

## CLI

```bash
# Database
python -m mkb setup
python -m mkb reset-db

# Ingestion
python -m mkb ingest ./data/papers/smith2024 --label "Smith 2024"
python -m mkb sync --root-dir ./data/papers

# Processing
python -m mkb process
python -m mkb process --project-id <uuid>

# Knowledge extraction
python -m mkb extract                              # all pending
python -m mkb extract --project-id <uuid>           # one project
python -m mkb extract --max-passes 3               # multi-pass
python -m mkb extract --model openai/gpt-4o         # override model
python -m mkb extraction-history <project_id>       # view pass history

# Listing
python -m mkb projects
python -m mkb assets --project-id <uuid>
python -m mkb frames
python -m mkb frame <project_id>

# Spaces & Projections
python -m mkb space create --name catalysis --domain catalysis --schema-file schema.json
python -m mkb space load space_definition.json
python -m mkb space list
python -m mkb space show catalysis
python -m mkb project-run --space <id> --project-id <uuid>
python -m mkb project-run --space <id> --all
python -m mkb projections --space-id <uuid>

# Feedback
python -m mkb feedback --project-id <uuid> --status OPEN
python -m mkb review-feedback --project-id <uuid>
python -m mkb resolve-feedback <feedback_id> --status RESOLVED --notes "Fixed"

# Projection Review (multi-agent consolidation)
python -m mkb review-projections --space <id> --project-id <uuid>
python -m mkb review-projections --space <id> --all
python -m mkb reviewed-projections --space-id <uuid>
python -m mkb reviewed-projection <reviewed_projection_id>

# UI
python -m mkb ui --port 8501
```

## LLM Configuration

Knowledge extraction uses google-adk with LiteLLM. Configure in `.env`:

```bash
MKB_EXTRACTION_MODEL=openai/qwen-plus
OPENAI_API_KEY=<your_key>
OPENAI_API_BASE=<your_openai_compatible_base_url>
```

## Project Structure

```
src/mkb/
├── api.py                          # Primary Python interface
├── cli.py                          # CLI (thin wrapper around api)
├── config.py                       # Settings from .env
├── db/
│   ├── engine.py                   # SQLAlchemy engine + init_db()
│   └── models.py                   # ORM models (12 tables + enums)
├── storage/
│   └── s3.py                       # MinIO upload/download/exists/delete
├── ingest/
│   └── worker.py                   # CAS ingestion (SHA256, MIME, batching)
├── processors/
│   ├── base.py                     # Abstract Processor + ProcessingResult
│   ├── coordinator.py              # Routes assets to processors
│   ├── pdf_processor.py
│   ├── text_processor.py
│   ├── dataframe_processor.py
│   └── image_processor.py
├── agents/
│   ├── extraction.py               # KB extraction agent + multi-pass orchestration
│   ├── review.py                   # Review agent for multi-turn extraction
│   ├── projection.py               # Projection agent (space-specific extraction)
│   ├── projection_reviewer.py      # Projection reviewer (multi-agent consolidation)
│   ├── projection_fixer.py         # Fixer sub-agent (source verification)
│   ├── feedback_reviewer.py        # Feedback review agent
│   ├── dev_agent.py                # Dev agent interface (design only)
│   ├── runner.py                   # Generic AgentRunner wrapper
│   ├── prompts/                    # Agent prompts
│   │   ├── kb_extraction.py        # Flexible KB extraction prompt
│   │   ├── review.py               # Review pass prompt
│   │   ├── projection.py           # Projection prompt builder
│   │   ├── projection_review.py    # Projection reviewer prompt
│   │   ├── projection_fixer.py     # Fixer sub-agent prompt
│   │   └── feedback_review.py      # Feedback review prompt
│   └── tools/                      # Agent tool functions
│       ├── reading.py              # Reading tools (markdown, dataframe, image, search)
│       ├── frames.py               # Frame save/get/update tools
│       ├── projection.py           # Projection save + flag_for_feedback
│       ├── projection_review.py    # Projection review + re-extraction tools
│       └── feedback.py             # Feedback query + resolve tools
├── spaces/
│   └── registry.py                 # Space CRUD operations
├── feedback/
│   └── manager.py                  # Feedback CRUD + resolution
└── ui/
    ├── app.py                      # Streamlit entry point
    ├── pages/                      # UI pages
    │   ├── projects.py
    │   ├── frames.py
    │   ├── projections.py
    │   └── feedback.py
    └── components/                 # Reusable UI components
        ├── frame_viewer.py
        └── graph_viz.py
```

## Database Tables

| Table | Purpose |
|---|---|
| `research_projects` | One per research package (paper + supplementary) |
| `assets` | One row per unique raw file (SHA256 deduplicated) |
| `project_assets` | Many-to-many link between projects and assets |
| `processed_assets` | One row per successful conversion output |
| `processing_logs` | Audit trail for processing attempts |
| `knowledge_frames` | One structured frame per project (JSONB content + metadata) |
| `extraction_passes` | Audit trail for each extraction/review pass |
| `spaces` | Domain-specific extraction configurations |
| `projections` | Results of projecting frames through spaces |
| `reviewed_projections` | Consolidated, corrected projection results from multi-agent review |
| `feedbacks` | Feedback items between agents |

## Services

| Service | URL | Credentials |
|---------|-----|------------|
| MinIO Console | http://localhost:9001 | minioadmin / minioadmin |
| MinIO S3 API | http://localhost:9000 | minioadmin / minioadmin |
| PostgreSQL | localhost:5432 | mkb / mkb_dev |
| Streamlit UI | http://localhost:8501 | — |
