# mat-know-base

A self-hosted system for ingesting scientific papers and related data into a queryable knowledge base. Files are stored immutably using content-addressable storage (SHA256 deduplication), with metadata tracked in PostgreSQL + pgvector and raw binaries in MinIO (S3-compatible). A processing pipeline converts raw files into LLM-readable Markdown, queryable Parquet dataframes, and structured image metadata.

## Architecture

```
data/inbox/              You drop files here
    │
    ▼
┌──────────┐  SHA256   ┌──────────────────────────────────┐
│ Ingestion├──────────►│  MinIO (S3)                      │
│  Worker  │           │  raw/       ← immutable originals │
└────┬─────┘           │  processed/ ← converted outputs  │
     │ metadata        └──────────────────────────────────┘
     ▼                          ▲
┌──────────────────┐            │  upload converted files
│  PostgreSQL      │            │
│  + pgvector      │   ┌────────┴──────────┐
│                  │   │ Processing Pipeline│
│  assets          │◄──┤  PDF → .md        │
│  processed_assets│   │  DOCX/TXT → .md   │
│  processing_logs │   │  CSV/XLSX → .parq │
│                  │   │  Image → .json    │
│  ingestion_batches│  └───────────────────┘
│  batch_assets    │
│  knowledge_nodes │  ← extracted entities (Phase 4)
│  knowledge_edges │  ← relationships (Phase 4)
└──────────────────┘
```

## Prerequisites

- Python 3.10+
- Docker & Docker Compose
- `libmagic` (usually pre-installed on Linux; `brew install libmagic` on macOS)

## Quick Start

```bash
# 1. Clone and enter the project
cd mat_know_base

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Copy environment config
cp .env.example .env

# 4. Install the package
pip install -e ".[dev,processing]"

# 5. Start infrastructure (PostgreSQL + MinIO)
make up

# 6. Run database migrations
make migrate
```

## Usage

### Ingesting Files

The standard workflow for scientific papers: **put all related files for one paper in a single folder**, then ingest that folder. The ingestion worker automatically creates a **batch** that groups them together.

```bash
# Ingest a folder of related files (paper PDF + supplementary + raw data)
# The folder name becomes the batch label
make ingest dir=./data/papers/smith2024_catalysis

# Or with a custom label
python -m mkb.cli ingest ./data/papers/smith2024_catalysis --label "Smith 2024 - Catalysis"
```

**Recommended folder structure for papers:**
```
data/papers/
├── smith2024_catalysis/
│   ├── smith2024_main.pdf
│   ├── smith2024_supplementary.pdf
│   ├── figure_data.csv
│   └── xrd_raw.dat
├── chen2023_perovskites/
│   ├── chen2023.pdf
│   └── lattice_params.xlsx
```

Each subfolder → one `make ingest dir=...` call → one batch in the DB.

### Processing Files

After ingestion the raw files sit in MinIO unmodified. Run the processing pipeline to convert them into formats usable by an LLM or downstream tooling.

| Input type | Output format | Location |
|---|---|---|
| PDF | Markdown (`.md`) + extracted files | `processed/<batch_id>/<asset_id>/...` |
| DOCX | Markdown (`.md`) | `processed/<batch_id>/<asset_id>/MARKDOWN.md` |
| TXT / MD | Markdown (`.md`) | `processed/<batch_id>/<asset_id>/MARKDOWN.md` |
| CSV / TSV | Parquet (`.parquet`) | `processed/<batch_id>/<asset_id>/DATAFRAME.parquet` |
| XLSX / XLS | Parquet (`.parquet`) | `processed/<batch_id>/<asset_id>/DATAFRAME.parquet` |
| JSON (tabular) | Parquet (`.parquet`) | `processed/<batch_id>/<asset_id>/DATAFRAME.parquet` |
| Images (PNG/JPG/…) | JSON metadata (`.json`) | `processed/<batch_id>/<asset_id>/IMAGE.json` |
| Plain `.txt` with delimited columns | Parquet like CSV | same as DATAFRAME above |

The pipeline is **idempotent**: re-running it on a file that has already been converted (same input, same output hash) simply skips it.

```bash
# Process all raw assets not yet converted
python -m mkb.cli process-all

# Same, with a per-file status summary
python -m mkb.cli process-all -v

# Process only the first N assets (useful for testing)
python -m mkb.cli process-all --limit 10

# Process a single asset by its UUID
python -m mkb.cli process-asset <asset_id>

# Clear processed outputs for one asset / one batch / everything
python -m mkb.cli clear-processed --asset-id <asset_id>
python -m mkb.cli clear-processed --batch-id <batch_id>
python -m mkb.cli clear-processed --all -y
```

#### Where to find the processed data

**In MinIO** — browse to http://localhost:9001, open the **`processed`** bucket. Files are stored at:
```
processed/<batch_uuid>/<asset_uuid>/...
# examples:
processed/1524e0c7-e0f9-41f6-b0bd-540282c10c58/99168fb7-899a-4548-bca8-15a646e929a4/s41563-026-02499-5.md
processed/1524e0c7-e0f9-41f6-b0bd-540282c10c58/99168fb7-899a-4548-bca8-15a646e929a4/images/<image>.jpg
processed/1524e0c7-e0f9-41f6-b0bd-540282c10c58/99168fb7-899a-4548-bca8-15a646e929a4/tables/<table>.*
```

**On local disk** — processed outputs are mirrored to:
```
data/processed/<batch_uuid>/<asset_uuid>/
```
So for one raw batch with 3 PDFs, you get 3 subfolders (one per PDF asset UUID), and each folder contains its markdown plus `images/` and `tables/` artifacts when MinerU produced them.

**In PostgreSQL** — the `processed_assets` table has a row for every successful conversion, including the S3 key, output size, content hash, and conversion metadata (page count, column names, image dimensions, etc.). The raw asset's `metadata` JSONB column also gets a `processing` key added that records the last conversion result and the `processed_asset_id` so you can link them without a join.

```bash
# List all processed assets
python -m mkb.cli processed-list

# Show full details (S3 path, type, metadata) for one processed asset
python -m mkb.cli processed-info <processed_asset_id>

# Clear processed outputs only; raw assets stay untouched
python -m mkb.cli clear-processed --asset-id <asset_id>
python -m mkb.cli clear-processed --batch-id <batch_id>
python -m mkb.cli clear-processed --all
```

`clear-processed` removes only the processed layer:
- rows in `processed_assets`
- related `processing_logs`
- local files under `data/processed/...`
- processed objects in the MinIO `processed` bucket

It does **not** delete raw files in the `raw` bucket or rows in `assets`.

#### PDF processing — MinerU

For PDFs the pipeline use **MinerU** (better table, figure, and multi-column handling).


### Listing Data

```bash
# List all assets (brief)
make list

# List assets grouped by batch (see which files belong together)
python -m mkb.cli batches

# Show detailed info for a specific asset
python -m mkb.cli info <asset_id>
python -m mkb.cli info <sha256_prefix>

# Show contents of a specific batch
python -m mkb.cli batch-info <batch_id>
```

### Deleting Test/Debug Data

```bash
# Delete a single asset (removes from DB + MinIO)
python -m mkb.cli delete <asset_id>

# Delete an entire batch and all its assets
python -m mkb.cli delete-batch <batch_id>

# Nuclear option: wipe ALL data (DB rows + MinIO objects) for a clean start
python -m mkb.cli purge --yes
```

### Other Commands

```bash
make up          # Start Docker services
make down        # Stop Docker services
make logs        # Tail service logs
make migrate     # Apply DB migrations
make test        # Run tests
```

## Project Structure

```
src/mkb/
├── cli.py              # Command-line interface
├── config.py           # Settings from .env
├── db/
│   ├── engine.py       # SQLAlchemy engine (async + sync)
│   └── models.py       # ORM models (Asset, ProcessedAsset, ProcessingLog, Batch, …)
├── storage/
│   └── s3.py           # MinIO upload/download/exists/delete
├── ingest/
│   └── worker.py       # CAS ingestion (SHA256, MIME detection, batching)
├── processors/
│   ├── base.py         # Abstract Processor base class and ProcessingResult
│   ├── coordinator.py  # Routes assets to processors, idempotency, metadata markers
│   ├── pdf_processor.py     # PDF → Markdown (MinerU + pdfplumber fallback)
│   ├── text_processor.py    # TXT / MD / DOCX → Markdown
│   ├── dataframe_processor.py  # CSV / TSV / XLSX / tabular JSON → Parquet
│   └── image_processor.py   # Images → JSON metadata (+ optional OCR)
└── agents/
    └── tools.py        # Functions for google-adk agent integration
```

## Key Design Decisions

- **Content-Addressable Storage:** Raw files stored by SHA256 hash. Re-uploading the same file costs 0 extra bytes.
- **Batch Grouping:** Each `ingest` call creates a batch. Related files (paper + supplementary + data) stay linked.
- **Separate raw / processed buckets:** Raw files in `raw/` are never overwritten. Converted outputs live in `processed/` so the two layers can never mix.
- **Idempotent processing:** A conversion is only written once. If the output hash matches an existing `processed_assets` row the run is skipped.
- **Conversion trackers on raw assets:** After processing, the raw asset's `metadata->processing` JSONB key is updated with the last status, processing type, and `processed_asset_id` so both ends of the link are immediately visible without a join.
- **pgvector Ready:** The `embedding` columns on `assets` and `knowledge_nodes` are ready for semantic search once an embedding model is integrated.
- **Agent-Ready:** `mkb.agents.tools` exposes `list_unprocessed_assets()`, `fetch_raw_binary()`, and `update_knowledge_node()` for `google-adk` integration.

## Database Tables

| Table | Purpose |
|---|---|
| `assets` | One row per unique raw file (SHA256 deduplicated) |
| `ingestion_batches` | Groups related files ingested together |
| `batch_assets` | Many-to-many link between batches and assets |
| `processed_assets` | One row per successful conversion output |
| `processing_logs` | Audit trail: every processing attempt (SUCCESS / SKIPPED / FAILED) |
| `knowledge_nodes` | Extracted scientific entities (Phase 4 placeholder) |
| `knowledge_edges` | Directed relationships between nodes (Phase 4 placeholder) |

## Services

| Service | URL | Credentials |
|---------|-----|------------|
| MinIO Console | http://localhost:9001 | minioadmin / minioadmin |
| MinIO S3 API | http://localhost:9000 | minioadmin / minioadmin |
| PostgreSQL | localhost:5432 | mkb / mkb_dev |

### MinIO Buckets

| Bucket | Contents |
|--------|----------|
| `raw` | Immutable original files (CAS-keyed by SHA256) |
| `processed` | Converted outputs keyed as `<batch_uuid>/<asset_uuid>/...` |
| `archive` | Reserved for archived/retired assets |
| `temp` | Temporary working data |
