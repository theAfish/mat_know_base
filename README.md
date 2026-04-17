# mat-know-base

A self-hosted system for ingesting scientific papers and related data into a structured knowledge base. Files are stored immutably using content-addressable storage (SHA256 deduplication), with metadata tracked in PostgreSQL + pgvector and raw binaries in MinIO (S3-compatible). A processing pipeline converts raw files into LLM-readable formats. An LLM agent then extracts structured **knowledge frames** — one per research package — containing concepts, experimental data, materials, methods, synthesis routes, and evidence-level-tagged statements.

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
│  ingestion_batches│     │  IMG  → .json      │
│  batch_assets   │       └───────────────────┘
│                 │
│  knowledge_frames│  ◄── LLM extraction agent
└─────────────────┘       (one frame per batch)
```

### Data Flow

1. **Ingest** — Raw files are SHA256-deduplicated, uploaded to MinIO, registered in PostgreSQL as a batch
2. **Process** — Raw files are converted to LLM-readable formats (Markdown, Parquet, JSON metadata)
3. **Extract** — An LLM agent reads processed data and produces one **knowledge frame** per batch
4. **Query** *(future)* — Downstream tools extract formatted databases and knowledge graphs from frames

### Knowledge Frame

Each batch (research package) produces one knowledge frame containing:

- **Paper metadata** — title, authors, journal, year, DOI
- **Concepts** — key scientific concepts with descriptions
- **Materials** — materials studied with chemical formulas and properties
- **Experimental data** — measurements with values, units, conditions, and methods
- **Methods** — experimental/computational techniques used
- **Synthesis routes** — input materials → output materials with conditions
- **Statements** — scientific claims and findings
- **Relationships** — subject-predicate-object triples between concepts

Every extracted item is tagged with an **evidence level**:
- **Level 1**: Causal experimental evidence
- **Level 2**: Direct experimental observation
- **Level 3**: Correlative evidence
- **Level 4**: Predicted / inferred

## Prerequisites

- Python 3.10+
- Docker & Docker Compose
- `libmagic` (usually pre-installed on Linux; `brew install libmagic` on macOS)

## Using Docker

This project includes a `docker-compose.yaml` to run the required infrastructure (PostgreSQL + `pgvector` and MinIO). You can use the provided `Makefile` targets or `docker compose` directly to manage the services.

Start services:
```bash
make up
# or
docker compose up -d
```

Stop services:
```bash
make down
# or
docker compose down
```

View status and logs:
```bash
docker compose ps
make logs
# or
docker compose logs -f
```

Service endpoints:
- MinIO console: http://localhost:9001  (minioadmin / minioadmin)
- MinIO S3 API: http://localhost:9000
- PostgreSQL: localhost:5432 (user: `mkb`, password: `mkb_dev`, database: `mkb`)

MinIO buckets: the compose file includes a one-shot `minio-init` service which creates the required buckets. It runs during `make up` when the `minio` service becomes healthy. To re-run it manually:
```bash
docker compose run --rm minio-init
```

Notes:
- Copy `.env.example` to `.env` before starting the stack (see Quick Start below).
- The Python application is not containerized by default. If you'd like, I can add a `Dockerfile` and a `docker-compose` service for the app so the whole stack runs in containers.

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

# Ensure DB tables exist
api.setup()

# Ingest a directory of files as one research package
result = api.ingest("./data/papers/smith2024_catalysis", label="Smith 2024")

# Process all raw assets into LLM-readable formats
api.process()

# Run LLM extraction on all unextracted batches
api.extract()

# Get the knowledge frame for a batch
frame = api.get_frame(batch_id="...")
print(frame["content"]["materials"])
print(frame["content"]["experimental_data"])

# List all frames
for f in api.list_frames():
    print(f["batch_id"], f["status"], f["extraction_summary"])

# List batches and assets
api.list_batches()
api.list_assets(batch_id="...")
```

## CLI (Secondary Interface)

The CLI wraps `mkb.api` for terminal use.

```bash
# Database
python -m mkb setup                    # Create tables
python -m mkb reset-db                 # Drop and recreate (destructive!)

# Ingestion
python -m mkb ingest ./data/papers/smith2024 --label "Smith 2024"

# Processing
python -m mkb process                  # Process all pending
python -m mkb process --batch-id <id>  # Process one batch

# Knowledge extraction
python -m mkb extract                  # Extract all pending
python -m mkb extract --batch-id <id>  # Extract one batch
python -m mkb extract --model openai/gpt-4o  # Override model

# Listing
python -m mkb batches                  # List batches
python -m mkb assets                   # List assets
python -m mkb assets --batch-id <id>   # Assets in a batch
python -m mkb frames                   # List knowledge frames
python -m mkb frame <batch_id>         # Show full frame (JSON)
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
├── api.py              # Primary Python interface
├── cli.py              # CLI (thin wrapper around api)
├── config.py           # Settings from .env
├── db/
│   ├── engine.py       # SQLAlchemy engine + init_db()
│   └── models.py       # ORM models (Asset, KnowledgeFrame, etc.)
├── storage/
│   └── s3.py           # MinIO upload/download/exists/delete
├── ingest/
│   └── worker.py       # CAS ingestion (SHA256, MIME detection, batching)
├── processors/
│   ├── base.py         # Abstract Processor + ProcessingResult
│   ├── coordinator.py  # Routes assets to processors
│   ├── pdf_processor.py
│   ├── text_processor.py
│   ├── dataframe_processor.py
│   └── image_processor.py
└── agents/
    ├── extraction.py   # LLM agent factory and runner
    └── tools.py        # Agent tools (reading + frame writing)
```

## Database Tables

| Table | Purpose |
|---|---|
| `assets` | One row per unique raw file (SHA256 deduplicated) |
| `ingestion_batches` | Groups related files ingested together |
| `batch_assets` | Many-to-many link between batches and assets |
| `processed_assets` | One row per successful conversion output |
| `processing_logs` | Audit trail for processing attempts |
| `knowledge_frames` | One structured frame per batch (JSONB content + metadata) |

## Services

| Service | URL | Credentials |
|---------|-----|------------|
| MinIO Console | http://localhost:9001 | minioadmin / minioadmin |
| MinIO S3 API | http://localhost:9000 | minioadmin / minioadmin |
| PostgreSQL | localhost:5432 | mkb / mkb_dev |
