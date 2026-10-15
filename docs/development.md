# Package development

Python 3.10+ is sufficient for the default development workflow:

```bash
make bootstrap
make doctor
make check
make build
```

`bootstrap` creates `.venv` and installs the editable package with development
tools. It does not create application configuration or start external services.
`install`, `lint`, `test`, `check`, and `build` operate on the Python package.
The wheel is written under `build/wheels/`. Override `PYTHON` to use an existing
environment; `BOOTSTRAP_PYTHON` selects the interpreter used to create `.venv`.

The base package has no database driver or web server dependency. Install `.[sql]`
to use the SQLAlchemy adapter; SQLite needs no separate service. Database-specific,
object storage, and application integrations have their own optional extras.
An embedding application chooses and supplies its storage interfaces, parsers,
agent callbacks, spaces, and post-processors. See [Python API](python-api.md).

## Validation

`make test` / `make test-python` run the portable SDK, storage, and extraction
contracts listed in `CORE_TESTS` in the Makefile. `make check` also runs Python
linting. These checks need neither model credentials nor a database server.
Development dependencies include SQLAlchemy to exercise the SQL adapter locally.

Distribution CI builds a wheel and sdist, installs the base wheel into a separate
environment, and exercises the custom-storage example outside the repository.
It then installs the SQL extra and runs the extraction and SQLite examples.

The full legacy application suite remains available with `make test-app` after
installing `.[all,dev]`. Frontend checks are explicit: `make lint-frontend` and
`make test-frontend` (which builds the bundle). Frontend CI runs only when its
files or workflow change, or when manually requested.

## Optional legacy application

The existing FastAPI/React application remains as an optional integration. Its
PostgreSQL schema, model providers, and any object storage services are managed
by the embedding/deployment environment. This repository does not start or stop
those services.

```bash
make install-app       # installs application extras and npm dependencies
# Review .env and point it at externally managed services.
make doctor-app        # read-only application dependency and endpoint checks
make dev               # starts FastAPI and Vite
```

The frontend requires Node.js 20+ and npm. Some document parsers also need system
libraries such as `libmagic` and Tesseract. `make server` starts just the API;
`make build-frontend` builds just React. `NPM` can be overridden.

`make dev` searches for an available API port starting at 8000 and shares it with
the Vite proxy. Override the starting port with `API_PORT=8001`. Vite starts at
5173 and tries subsequent ports when occupied. Ctrl+C or either service exiting
stops both process groups. The standalone `make server` uses its specified port.

The retired historical Alembic chain is not a database provisioning mechanism.
Use a verified current snapshot for the legacy application. For isolated local
UI testing against an already provisioned PostgreSQL server, the optional helper
creates tables only in the fixed local demo database:

```bash
MKB_PG_DATABASE=mkb_demo_acceptance .venv/bin/python scripts/bootstrap_demo_schema.py
MKB_PG_DATABASE=mkb_demo_acceptance make server API_PORT=8503
```

The helper refuses other database names and non-local hosts. It never drops
tables. Portable SDK clients initialize only their own `mkb_*` tables when
explicitly requested by the caller.

## Legacy snapshots

Snapshot scripts remain for existing PostgreSQL application data. They are
optional operational tools, not a package-wide persistence requirement. Install
the application extras and PostgreSQL client programs (`pg_dump`, `psql`,
`createdb`, `dropdb`), then configure `MKB_PG_HOST`, `MKB_PG_PORT`, `MKB_PG_USER`,
`MKB_PG_PASSWORD`, and `MKB_PG_DATABASE` through the normal application settings.
No container engine is used. Standard libpq options such as `PGSSLMODE` and
`PGPASSFILE` are preserved.

```bash
make pack out=backup.tar.gz
make restore-drill file=backup.tar.gz
make unpack file=backup.tar.gz flags=--validate-only
# Explicit live replacement still requires typing the target database name:
make unpack file=backup.tar.gz flags=--confirm-replace
```

Validation checks archive checksums, restores SQL into a temporary database,
checks references against archived object/file payloads, and removes the
temporary database. The configured PostgreSQL role must be able to create and
drop its temporary databases. Validation leaves live objects and files unchanged.
`--local-only --validate-only` can check local payloads without PostgreSQL tools.

`restore-drill` now requires an existing archive and uses this same validation
path. It no longer provisions a separate object storage service or generates
live/restored SDK inventory comparisons. Snapshot checksums and object-reference
validation remain; deployments can add their own end-to-end restore exercises.

Local artifacts under `data/`, `.debug/`, and `logs/` are generated files unless
deliberately promoted to `examples/` or `tests/fixtures/`.
