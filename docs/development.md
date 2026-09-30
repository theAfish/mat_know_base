# Developer setup

## Clean clone

Install Python 3.10+, Node.js 20+, npm, Docker Compose, and `libmagic`, then run:

```bash
make bootstrap
```

This creates `.venv`, upgrades its pip, installs `.[all,dev]`, runs `npm ci` from the
committed lockfile, and creates `.env` only if absent. Edit `.env`, then:

```bash
make up       # waits for healthy PostgreSQL/MinIO
make doctor   # verifies the whole local environment
make dev      # FastAPI and Vite; Ctrl+C stops both
```

All Make targets use `PYTHON ?= .venv/bin/python` and Python modules (`python -m
...`) consistently. Override it explicitly for tooling or CI. `BOOTSTRAP_PYTHON`
is used only to create the environment, and `NPM` can likewise be overridden.

## Validation and build

```bash
make lint
make test-python
make test-frontend
make check
make build
```

`make build` writes a Python wheel under `build/wheels/` and builds the production
React bundle. CI should begin with `make bootstrap` (or reproduce its pinned npm
install and editable dev install) before invoking these targets.

The base wheel depends only on Pydantic and SQLAlchemy. Backend and application
dependencies are opt-in through `postgres`, `s3`, `pdf`, `server`, `materials`,
`neo4j`, or `all`. Distribution CI builds both wheel and sdist, installs the wheel
into a separate environment, and runs `examples/portable_quickstart.py` without the
repository on its import path.

## Database provisioning

The historical Alembic chain was retired after the local database reached its final
supported revision. This repository does not provision or upgrade the legacy materials
schema. Start from a verified current database snapshot; explicit SDK clients create
only their portable `mkb_*` tables with `kb.initialize()`.

For isolated local UI acceptance when no snapshot is available, use the disposable
demo schema instead. It creates the existing ORM tables in a separate database,
not a production migration or a substitute for a verified snapshot:

```bash
docker compose up -d postgres minio minio-init
MKB_PG_DATABASE=mkb_demo_acceptance .venv/bin/python scripts/bootstrap_demo_schema.py
MKB_PG_DATABASE=mkb_demo_acceptance .venv/bin/python -m mkb.cli api --host 127.0.0.1 --port 8503
```

The bootstrap refuses any database name other than `mkb_demo_acceptance` or a
non-local PostgreSQL host. It is additive and repeatable; it never drops tables.
Pass the same `MKB_PG_DATABASE` override to any launcher used for the MKB API.
The demo database starts empty, so extraction still requires an available model
and document processing dependencies. Do not treat demo results as production data.

## Runtime files and legacy surfaces

Local artifacts live under `data/`, `.debug/`, `logs/`, and Docker volumes. Treat
exports as generated unless deliberately promoted to `examples/` or `tests/fixtures/`.

React (`frontend/`) is the only bundled UI. Legacy canonical-workflow adapters remain
read-compatible while their retained records are exported or retired.
