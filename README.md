# mat-know-base

Materials Knowledge Base is a local-first tool for turning scientific papers and
supplementary files into structured knowledge.

It can:

- ingest a folder of papers and data files as a research project
- store raw files in MinIO and metadata in PostgreSQL/pgvector
- process PDFs, text files, spreadsheets, and images into LLM-readable outputs
- extract project-level knowledge frames with an LLM agent
- project frames into domain-specific schemas called spaces
- build and review a shared concept knowledge graph
- browse and manage the library through a React web UI

## Requirements

- Python 3.10+
- Node.js and npm
- Docker and Docker Compose
- `libmagic`

On macOS, install `libmagic` with:

```bash
brew install libmagic
```

## First-Time Setup

Clone the repo, then run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Create your local environment file:

```bash
cp .env.example .env
```

Edit `.env` and add your LLM credentials:

```bash
MKB_EXTRACTION_MODEL=openai/qwen-plus
OPENAI_API_KEY=your_key_here
OPENAI_API_BASE=your_openai_compatible_base_url
```

For OpenAI directly, `OPENAI_API_KEY` is usually enough. For other
OpenAI-compatible providers, set both `OPENAI_API_KEY` and `OPENAI_API_BASE`.

Start PostgreSQL, pgvector, and MinIO:

```bash
make up
```

Create the database tables:

```bash
mkb setup
```

Install the frontend dependencies:

```bash
cd frontend
npm install
cd ..
```

## Run The App

Start the backend API and React frontend together:

```bash
source .venv/bin/activate
make up
bash scripts/dev.sh
```

Open:

- React UI: http://127.0.0.1:5173
- Backend API: http://127.0.0.1:8503
- MinIO console: http://127.0.0.1:9001

MinIO local login:

```text
minioadmin / minioadmin
```

Press `Ctrl+C` in the `scripts/dev.sh` terminal to stop the app servers.

Stop Docker services with:

```bash
make down
```

## Basic Workflow

Put one paper and its supplementary files in a folder, for example:

```text
data/papers/smith2024/
  paper.pdf
  supplement.csv
  notes.txt
```

Then run:

```bash
mkb ingest ./data/papers/smith2024 --label "Smith 2024"
mkb process
mkb extract --max-passes 2
```

Check the result:

```bash
mkb projects
mkb frames
mkb frame <project_id>
```

You can also do the same from the React UI at http://127.0.0.1:5173.

## Python API

The main Python interface is `mkb.api`.

```python
from mkb import api

api.setup()

project = api.ingest("./data/papers/smith2024", label="Smith 2024")
api.process(project_id=project["project_id"])
api.extract(project_id=project["project_id"], max_passes=2)

frame = api.get_frame(project["project_id"])
print(frame["content"].keys())
```

See [examples/basic_usage.py](examples/basic_usage.py) for a longer walkthrough.

## Common CLI Commands

Database:

```bash
mkb setup
mkb reset-db
```

Ingest and process:

```bash
mkb ingest ./data/papers/smith2024 --label "Smith 2024"
mkb sync --root-dir ./data/papers
mkb process
mkb process --project-id <project_id>
mkb processed --project-id <project_id>
```

Extract and inspect knowledge frames:

```bash
mkb extract
mkb extract --project-id <project_id> --max-passes 2
mkb projects
mkb assets --project-id <project_id>
mkb frames
mkb frame <project_id>
mkb extraction-history <project_id>
mkb search "hydroxyapatite nucleation"
```

Run the web services separately:

```bash
mkb api --host 127.0.0.1 --port 8503
cd frontend && npm run dev
```

The legacy Streamlit UI is still available:

```bash
mkb ui --port 8501
```

## Spaces And Projections

A space defines a domain-specific schema. A projection applies that schema to a
knowledge frame.

Create a space from a JSON definition:

```bash
mkb space load examples/spaces/computational_materials_qa.json
```

List and inspect spaces:

```bash
mkb space list
mkb space show <space_id_or_name>
```

Run projections:

```bash
mkb project-run --space <space_id_or_name> --project-id <project_id>
mkb project-run --space <space_id_or_name> --all
mkb projections --space-id <space_id>
mkb projection <projection_id>
```

Review and consolidate projections:

```bash
mkb review-projections --space <space_id_or_name> --project-id <project_id>
mkb review-projections --space <space_id_or_name> --all
```

## Knowledge Graph

Build a shared concept graph from completed knowledge frames:

```bash
mkb kg-clear
mkb kg-extract
mkb kg-show
```

For one project:

```bash
mkb kg-clear --project-id <project_id>
mkb kg-extract --project-id <project_id>
mkb kg-show --project-id <project_id>
```

Review the graph:

```bash
mkb kg-review --mode global
mkb kg-review --mode local --seed-count 10
mkb kg-review-counts
```

## Workflow Extraction

The repo also includes workflow extraction, review, schema curation, and
maintenance commands:

```bash
mkb workflow-review <extraction_id> --status active
mkb workflow-correct <extraction_id> corrected_graph.json --reason "..." --author "..." --evidence "..."
mkb schema-curate
mkb schema-proposals
mkb schema-review <proposal_id> --approve --reviewer "..."
mkb workflow-reextract <project_id> --reason low_quality_extraction --run
mkb workflow-recanonicalize <project_id> --run
mkb workflow-index
mkb workflow-search --source "..." --operation "..."
```

For design details, see:

- [docs/workflow-lifecycle-policy.md](docs/workflow-lifecycle-policy.md)
- [docs/workflow-card-architecture.md](docs/workflow-card-architecture.md)
- [docs/architecture-map.md](docs/architecture-map.md)

## Data Snapshots

The database and MinIO files are local runtime state, so they are not committed
to git. Use the helper scripts to move a local dataset between machines.

Create a snapshot:

```bash
make pack
```

Restore a snapshot:

```bash
make unpack file=mkb_data_YYYYMMDD.tar.gz
```

The first restore invocation validates only. To replace live data, run
`bash scripts/unpack_data.sh <archive> --confirm-replace` and type the database
name when prompted. See [the operator runbook](docs/operator-runbook.md).

Maintenance is dry-run-first:

```bash
mkb cleanup
mkb reconcile
```

## Development

Run Python tests:

```bash
pytest
```

Run frontend type checks and build:

```bash
cd frontend
npm run build
```

Run the project checks:

```bash
make check
```

Useful docs:

- [docs/development.md](docs/development.md)
- [docs/security.md](docs/security.md)
- [src/mkb/ui/README.md](src/mkb/ui/README.md)

## Troubleshooting

If the UI is empty, check that the API is running:

```bash
curl http://127.0.0.1:8503/api/projects?limit=5
```

If the API has no projects, ingest data first:

```bash
mkb ingest ./data/papers/smith2024 --label "Smith 2024"
```

If `scripts/dev.sh` fails, make sure both dependency installs completed:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
cd frontend && npm install
```
