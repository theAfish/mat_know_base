# mat-know-base

Materials Knowledge Base (MKB) is a local-first application for ingesting scientific
papers and supplementary files, processing them into LLM-readable artifacts, and
building structured knowledge frames, domain projections, workflows, and graphs.

The React application is the user interface. Canonical-workflow paths remain a limited
compatibility surface; see the [workflow lifecycle policy](docs/workflow-lifecycle-policy.md).

## Install during development

MKB is not yet published on PyPI. Install it directly from this repository instead.
The `dev` branch is the current shared development build:

```bash
python -m pip install --upgrade --force-reinstall \
  "mat-know-base @ git+https://github.com/theAfish/mat_know_base.git@dev"
```

Install optional integrations only when they are needed. For example, the full
materials application and HTTP server require:

```bash
python -m pip install --upgrade --force-reinstall \
  "mat-know-base[materials,server] @ git+https://github.com/theAfish/mat_know_base.git@dev"
```

`--force-reinstall` is intentional while the package version remains `0.1.0`; it makes
pip replace an older build from the moving development branch. For a reproducible bug
report, replace `dev` with the commit SHA being tested. Git must be installed and the
repository must be accessible to the user.

To develop or debug with the maintainers, use an editable checkout instead. Python code
changes then take effect without reinstalling:

```bash
git clone https://github.com/theAfish/mat_know_base.git
cd mat_know_base
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Use `-e ".[dev,materials,server]"` when working on the complete application. Rerun the
install command after changing dependencies or package metadata.

## Local quickstart

Requirements: Python 3.10+, Node.js 20+, npm, Docker with Compose, and `libmagic`.
For image OCR, install Tesseract. On macOS, `brew install libmagic tesseract`.

```bash
git clone https://github.com/theAfish/mat_know_base.git
cd mat_know_base
make bootstrap
```

Review `.env` and set an LLM credential. For an OpenAI-compatible provider:

```dotenv
MKB_EXTRACTION_MODEL=openai/qwen-plus
OPENAI_API_KEY=replace-me
OPENAI_API_BASE=https://provider.example/v1
```

Then start the infrastructure and application:

```bash
make up
make doctor
make dev
```

Open the React UI at <http://127.0.0.1:5173>. The API is at
<http://127.0.0.1:8503>, its interactive OpenAPI documentation at
<http://127.0.0.1:8503/docs>, and the MinIO console at
<http://127.0.0.1:9001>. Stop application servers with `Ctrl+C` and infrastructure
with `make down`.

`make bootstrap` creates `.venv`, installs Python and locked frontend dependencies,
and copies `.env.example` to `.env` without overwriting an existing file. Override
tools when necessary, for example `make bootstrap BOOTSTRAP_PYTHON=python3.12` or
`make test PYTHON=/path/to/python`.

## First workflow

Put a paper and its supplementary files in one directory:

```text
data/papers/smith2024/
  paper.pdf
  supplement.csv
  notes.txt
```

Run the commands through the project interpreter:

```bash
.venv/bin/python -m mkb.cli ingest data/papers/smith2024 --label "Smith 2024"
.venv/bin/python -m mkb.cli process
.venv/bin/python -m mkb.cli extract --max-passes 2
.venv/bin/python -m mkb.cli projects
```

The same workflow is available in the React UI. For library use, start with the
[detailed Python API guide](docs/python-api.md) and
[examples/basic_usage.py](examples/basic_usage.py).

For a Docker-free library project, install the lightweight base package and use
SQLite plus filesystem storage. The reusable example is in the Python API guide; from a
source checkout, it can also be run directly:

```bash
python -m pip install --upgrade --force-reinstall \
  "mat-know-base @ git+https://github.com/theAfish/mat_know_base.git@dev"
# From this repository checkout:
python examples/portable_quickstart.py
```

For backend integrations, install extras from Git as well, such as
`"mat-know-base[postgres,s3] @ git+https://github.com/theAfish/mat_know_base.git@dev"`.

When MKB is released on PyPI, the Git URLs above will be replaced by normal package
installs.

## Common development commands

```bash
make doctor       # read-only environment and dependency diagnostics
make lint         # Ruff and TypeScript checks
make test         # Python tests
make build        # Python wheel and production React bundle
make check        # complete local validation
```

## Documentation

Documentation is organized by role in the [documentation index](docs/README.md):

- Users and automation authors: [Python API](docs/python-api.md) and
  [HTTP API contract](docs/api-contract.md)
- Contributors: [developer setup](docs/development.md),
  [architecture and ownership](docs/architecture-map.md), and
  [contribution guide](CONTRIBUTING.md)
- Operators and security reviewers: [operator runbook](docs/operator-runbook.md),
  [backup and restore](docs/backup-restore.md), [upgrades](docs/upgrades.md), and
  [security model](docs/security.md)
