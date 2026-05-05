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

### Knowledge Graph Construction (Global Concept Graph)

Knowledge graph construction uses a dedicated KG extraction agent with one shared global space across all domains.

Design goals:
- **One global graph space**: all projects contribute to a shared graph so inter-domain links can emerge.
- **Concept-only nodes**: only scientific concepts become nodes.
- **Concept relations as edges**: edges encode directed concept-to-concept relations.
- **Details in references**: values/conditions/metadata are stored as references back to frame/database context, not turned into extra nodes.
- **Redundancy-aware build**: the agent checks existing graph content to reduce duplicate concepts/edges.

Recommended usage flow:
1. Extract knowledge frames first.
2. Optionally clear old KG outputs.
3. Run KG extraction.
4. Inspect merged graph output.

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
mkb setup

# 5. (Optional) Start the React frontend
cd frontend && npm install && npm run dev
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

# Knowledge graph construction (global concept graph)
api.clear_knowledge_graphs()
api.extract_knowledge_graph(project_id="...")
kg = api.get_knowledge_graph()
print(len(kg["graph"]["concepts"]), len(kg["graph"]["relations"]))

# Knowledge graph review (deduplication + quality cleanup)
api.review_knowledge_graph()                            # auto mode (random global or local)
api.review_knowledge_graph(mode="global", verbose=True) # full graph: standardize + dedup
api.review_knowledge_graph(mode="local", seed_count=15) # neighborhood review (least-reviewed first)
counts = api.get_graph_review_counts()                  # per-element review counters

# Search papers and data
results = api.search_library("enamel mineralization")
print(results["projects"])
print(results["assets"])
```

## CLI

```bash
# Database
mkb setup
mkb reset-db

# Ingestion
mkb ingest ./data/papers/smith2024 --label "Smith 2024"
mkb sync --root-dir ./data/papers

# Processing
mkb process
mkb process --project-id <uuid>

# Knowledge extraction
mkb extract                              # all pending
mkb extract --project-id <uuid>          # one project
mkb extract --max-passes 3               # multi-pass
mkb extract --model openai/gpt-4o        # override model
mkb extraction-history <project_id>      # view pass history

# Listing
mkb projects
mkb assets --project-id <uuid>
mkb search "enamel mineralization"
mkb search "csv supplement" --project-id <uuid>
mkb frames
mkb frame <project_id>

# Spaces & Projections
mkb space create --name catalysis --domain catalysis --schema-file schema.json
mkb space load space_definition.json
mkb space list
mkb space show catalysis
mkb project-run --space <id> --project-id <uuid>
mkb project-run --space <id> --all
mkb projections --space-id <uuid>

# Feedback
mkb feedback --project-id <uuid> --status OPEN
mkb review-feedback --project-id <uuid>
mkb resolve-feedback <feedback_id> --status RESOLVED --notes "Fixed"

# Projection Review (multi-agent consolidation)
mkb review-projections --space <id> --project-id <uuid>
mkb review-projections --space <id> --all
mkb reviewed-projections --space-id <uuid>
mkb reviewed-projection <reviewed_projection_id>

# Knowledge graph construction (global concept graph)
mkb kg-clear                                      # clear old KG projections (and legacy frame-graph sections)
mkb kg-extract                                    # build KG for all completed frames
mkb kg-extract --project-id <uuid>               # build KG for one project
mkb kg-extract --frame-id <uuid>                 # build KG for one frame
mkb kg-show                                       # show merged global concept graph
mkb kg-show --project-id <uuid>                  # merged graph filtered to one project

# Start the FastAPI backend (serves the React UI in production)
make server
```

## Search

Keyword search is available in all three interfaces:

- **UI**: The **Research Projects → Browse** view includes a search box for papers and ingested data assets.
- **Python API**: `api.search_library(query, limit=25, project_id=None)` returns matching projects and assets.
- **CLI**: `mkb search "keywords"` prints matching projects and assets, with optional `--project-id` scoping.

Search behavior:

- Queries are split into whitespace-separated keyword tokens.
- All tokens must match somewhere in a result.
- Project matches use project label and source path.
- Asset matches use filename, MIME type, and selected asset metadata fields.

## Knowledge Graph Quickstart

```bash
# 1) Make sure knowledge frames exist
mkb extract

# 2) Optional clean rebuild
mkb kg-clear

# 3) Construct concept graph projections
mkb kg-extract

# 4) Inspect merged graph
mkb kg-show
```

Project-scoped example:

```bash
mkb kg-clear --project-id <project_uuid>
mkb kg-extract --project-id <project_uuid>
mkb kg-show --project-id <project_uuid>
```

Notes:
- `kg-extract` clears existing KG projections for target frame(s) by default. Use `--no-clear-existing` to keep prior projection history.
- Legacy graph-like sections inside frame content are removed by default during cleanup/extraction. Use `--keep-legacy-frame-graphs` to skip that behavior.

## Knowledge Graph Review

After building the graph, a dedicated **Graph Review Agent** deduplicates concepts, standardizes relation naming, and prunes low-quality entries. It runs in two modes:

| Mode | What it does |
|------|-------------|
| **global** | Analyzes the full graph: groups similar relation names and standardizes them; finds and merges synonymous concept nodes across all projections |
| **local** | Selects the least-reviewed concepts as starting points, explores their neighborhood, verifies ambiguous entries against source knowledge frames, and fixes local issues |

Each run tracks how many times each node/edge was examined and modified in the `graph_element_reviews` table — always incremented by the orchestration script, never by the agent itself.

### Python API

```python
# Global mode: relation standardization + concept deduplication
api.review_knowledge_graph(mode="global", verbose=True)

# Local mode: deep-dive on least-reviewed concepts
api.review_knowledge_graph(mode="local", seed_count=15, verbose=True)

# Auto: randomly picks global or local each time (default)
api.review_knowledge_graph()

# Inspect per-element review counts
counts = api.get_graph_review_counts()
# counts["concepts"]["hydroxyapatite"] → {"times_examined": 3, "times_modified": 1, ...}
# counts["relations"]["amelotin||promotes||hydroxyapatite nucleation"] → {...}
```

### Review tools available to the agent

| Tool | Mode | Description |
|------|------|-------------|
| `get_concept_details` | both | Full concept record + all incoming/outgoing relations |
| `get_concept_neighbors` | both | Concept + 1-hop neighbors + relations |
| `get_relation_type_distribution` | both | Count of each distinct relation label |
| `search_graph_elements` | both | Keyword search across concept labels, aliases, relation names |
| `find_similar_concepts` | both | Token-overlap similarity search for near-duplicate concepts |
| `merge_concepts` | both | Merge N concepts into one canonical node across all projections |
| `standardize_relation_name` | both | Rename relation type(s) to a canonical form everywhere |
| `delete_concept` | both | Delete an isolated concept (rejects if relations still exist) |
| `delete_relation` | both | Delete a specific directed relation |
| `get_frame_content` | local | Read a source knowledge frame for concept verification |

### Knowledge Graph Output Shape

`mkb kg-show` and `api.get_knowledge_graph()` return a normalized concept graph:

```json
{
        "graph": {
                "concepts": [
                        {
                                "label": "Amelotin",
                                "aliases": ["AMTN"],
                                "source_project_ids": ["..."],
                                "source_frame_ids": ["..."],
                                "knowledge_refs": [
                                        {
                                                "project_id": "...",
                                                "frame_id": "...",
                                                "field_path": "...",
                                                "snippet": "..."
                                        }
                                ]
                        }
                ],
                "relations": [
                        {
                                "source": "Amelotin",
                                "relation": "promotes",
                                "target": "Hydroxyapatite nucleation",
                                "evidence_level": 2,
                                "source_project_id": "...",
                                "source_frame_id": "...",
                                "knowledge_ref": {
                                        "project_id": "...",
                                        "frame_id": "...",
                                        "field_path": "...",
                                        "snippet": "..."
                                }
                        }
                ]
        }
}
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
│   ├── knowledge_graph.py          # KG agent (global concept graph extraction)
│   ├── projection_reviewer.py      # Projection reviewer (multi-agent consolidation)
│   ├── projection_fixer.py         # Fixer sub-agent (source verification)
│   ├── feedback_reviewer.py        # Feedback review agent
│   ├── dev_agent.py                # Dev agent interface (design only)
│   ├── runner.py                   # Generic AgentRunner wrapper
│   ├── prompts/                    # Agent prompts
│   │   ├── kb_extraction.py        # Flexible KB extraction prompt
│   │   ├── review.py               # Review pass prompt
│   │   ├── projection.py           # Projection prompt builder
│   │   ├── knowledge_graph.py      # Concept-graph extraction prompt
│   │   ├── projection_review.py    # Projection reviewer prompt
│   │   ├── projection_fixer.py     # Fixer sub-agent prompt
│   │   └── feedback_review.py      # Feedback review prompt
│   └── tools/                      # Agent tool functions
│       ├── reading.py              # Reading tools (markdown, dataframe, image, search)
│       ├── frames.py               # Frame save/get/update tools
│       ├── projection.py           # Projection save + flag_for_feedback
│       ├── knowledge_graph.py      # Concept-graph tools + redundancy checks
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
| `feedbacks` | Feedback items between agents |
| `graph_element_reviews` | Per-element review counts (`times_examined`, `times_modified`) for graph nodes and edges |

## Data Sharing

Because the database and files are developed locally (papers, processed outputs, PostgreSQL, MinIO) and cannot be committed to git, two helper scripts let you snapshot and restore the entire local state.

### Pack (create a snapshot)

```bash
# Auto-named: mkb_data_YYYYMMDD_HHMMSS.tar.gz
make pack

# Custom filename
make pack out=my_dataset_v1.tar.gz

# Or run directly
bash scripts/pack_data.sh my_snapshot.tar.gz
```

What gets bundled:
- **PostgreSQL** — full `pg_dump` of the `mkb` database (schema + data)
- **MinIO buckets** — `raw`, `processed`, `archive`, `temp`
- **Local dirs** — `data/papers/`, `data/processed/`, `data/uploads/`, `data/inbox/`
- **manifest.json** — records timestamp, database name, and bucket list

Requirements: Docker (already needed), `tar`, `python3`.

### Unpack (restore from a snapshot)

```bash
# Full restore (interactive confirmation before dropping the DB)
make unpack file=mkb_data_20260429_120000.tar.gz

# Or run directly
bash scripts/unpack_data.sh mkb_data_20260429_120000.tar.gz
```

Partial restore flags:

| Flag | Effect |
|------|--------|
| `--pg-only` | Restore PostgreSQL only |
| `--minio-only` | Restore MinIO buckets only |
| `--local-only` | Restore local data dirs only |
| `--no-pg` | Skip PostgreSQL restore |
| `--no-minio` | Skip MinIO buckets |
| `--no-local` | Skip local data dirs |

After unpacking, run `alembic upgrade head` if the schema migration level differs between the snapshot and your current codebase.

### Typical workflow for onboarding a new developer

```bash
# 1. Clone repo and install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,processing]"

# 2. Start services
make up

# 3. Restore a shared snapshot
make unpack file=mkb_data_20260429_120000.tar.gz

# 4. Apply any pending migrations
alembic upgrade head
```

## Frontend (React + Vite)

A React 19 + Vite + TypeScript UI lives in `frontend/`. It replaces the legacy Streamlit UI and communicates with the FastAPI backend through a Vite dev-server proxy.

### Prerequisites

- Node.js 20+ and npm 10+
- Backend running (`make server`)

### Installation

```bash
cd frontend
npm install
```

### Development

```bash
# In one terminal — start the backend
make server

# In another terminal — start the Vite dev server
cd frontend
npm run dev
```

Open http://localhost:5173 (or the port shown in the Vite output).

All `/api/*` requests are proxied to `http://127.0.0.1:8503` by Vite, so no CORS configuration is needed during development.

### Production build

```bash
cd frontend
npm run build     # output goes to frontend/dist/
npm run preview   # serve the production build locally
```

### Pages

| Page | Route | Description |
|------|-------|-------------|
| Research Projects | `/` | Browse, upload, and manage research packages |
| Knowledge Frames | `/frames` | View extracted frames; run processing, extraction, projection, and graph pipelines per project |
| Projections | `/projections` | Aggregated projection table across all papers for a selected space |
| Knowledge Graph | `/graph` | Interactive force-directed concept graph (vis-network, Barnes-Hut physics); supports node/edge coloring by evidence level, review coverage, modification heat, and connectivity |
| Chat | `/chat` | LLM chat interface (experimental) |

### Tech stack

- **React 19** + **TypeScript**
- **Vite 8** (build tool + dev proxy)
- **Tailwind CSS v4** (CSS-first config, no `tailwind.config.js`)
- **vis-network 10** — knowledge graph visualization (same library as pyvis)
- **Zustand v4** — UI state management
- **Axios v1** — HTTP client

## Services

| Service | URL | Credentials |
|---------|-----|------------|
| MinIO Console | http://localhost:9001 | minioadmin / minioadmin |
| MinIO S3 API | http://localhost:9000 | minioadmin / minioadmin |
| PostgreSQL | localhost:5432 | mkb / mkb_dev |
| FastAPI backend | http://localhost:8503 | — |
| React UI (dev) | http://localhost:5173 | — |
