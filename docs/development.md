# Developer setup

## Clean clone

Install Python 3.10+, Node.js 20+, npm, Docker Compose, and `libmagic`, then run:

```bash
make bootstrap
```

This creates `.venv`, upgrades its pip, installs `.[all,dev]`, runs `npm ci` from the
committed lockfile, and creates `.env` only if absent. Edit `.env`, then:

```bash
make up       # waits for healthy PostgreSQL/MinIO, then migrates
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

## Database changes

Start PostgreSQL, edit SQLAlchemy models, then create a revision:

```bash
make migration msg="describe the schema change"
```

Review both upgrade and downgrade operations. Test forward migration with `make
migrate` and restoration against a disposable database. Migration changes require
database-owner review; see [CONTRIBUTING.md](../CONTRIBUTING.md).

Never rewrite a revision already shared or deployed. Add a new corrective revision.

## Runtime files and legacy surfaces

Local artifacts live under `data/`, `.debug/`, `logs/`, and Docker volumes. Treat
exports as generated unless deliberately promoted to `examples/` or `tests/fixtures/`.

React (`frontend/`) is current. Streamlit (`src/mkb/ui/`) and legacy canonical
workflow adapters are compatibility-only. Fix regressions there when necessary, but
place new behavior in services and React.
