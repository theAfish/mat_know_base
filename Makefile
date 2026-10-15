.PHONY: setup bootstrap install install-python install-app install-frontend doctor doctor-app ingest list batches info purge test lint test-python test-app lint-frontend test-frontend build build-frontend dev server check ci cleanup reconcile pack unpack restore-drill

.DEFAULT_GOAL := install

PYTHON ?= .venv/bin/python
BOOTSTRAP_PYTHON ?= python3
NPM ?= npm
export API_PORT ?= 8000
export PYTHONPATH := $(CURDIR)/src$(if $(PYTHONPATH),:$(PYTHONPATH))

# Portable package contracts; the legacy application has a separate full suite.
CORE_TESTS := tests/test_sdk.py tests/test_public_contract.py tests/test_adapters.py \
	tests/test_repositories.py tests/test_generic_repositories.py tests/test_registries.py \
	tests/test_pipelines.py tests/test_durable_pipeline_jobs.py tests/test_graph_sdk.py \
	tests/test_knowledge_lifecycle.py tests/test_extraction_core.py tests/test_storage_interfaces.py \
	tests/test_infra_boundaries.py tests/test_parser_adapters.py tests/test_snapshot_commands.py

# ── Clean-clone bootstrap ──────────────────────────────────────
setup: bootstrap

bootstrap:
	$(BOOTSTRAP_PYTHON) -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(MAKE) install
	@echo "Package environment ready. Run 'make check' or import mkb from your project."

install: install-python

install-python:
	$(PYTHON) -m pip install -e ".[dev]"

# Optional legacy application: provision its services externally before use.
install-app:
	$(PYTHON) -m pip install -e ".[all,dev]"
	$(MAKE) install-frontend
	@test -f .env || cp .env.example .env

install-frontend:
	cd frontend && $(NPM) ci

doctor:
	$(PYTHON) -m mkb.doctor

# ── Optional legacy application ─────────────────────────────────
doctor-app:
	$(PYTHON) -m mkb.doctor --app

# ── CLI shortcuts ───────────────────────────────────────────────
ingest:  ## usage: make ingest dir=./data/inbox
	$(PYTHON) -m mkb.cli ingest $(dir)

list:
	$(PYTHON) -m mkb.cli list

batches:
	$(PYTHON) -m mkb.cli batches

info:  ## usage: make info id=<asset_id or sha256_prefix>
	$(PYTHON) -m mkb.cli info $(id)

purge:
	$(PYTHON) -m mkb.cli purge

cleanup:
	$(PYTHON) -m mkb.cli cleanup

reconcile:
	$(PYTHON) -m mkb.cli reconcile

# ── Data sharing ────────────────────────────────────────────────
pack:  ## Create a portable snapshot: make pack [out=my_snapshot.tar.gz]
	PYTHON="$(PYTHON)" bash scripts/pack_data.sh $(if $(out),"$(out)",)

unpack:  ## Restore from snapshot: make unpack file=mkb_data_YYYYMMDD.tar.gz
	@[ -n "$(file)" ] || (echo "Usage: make unpack file=<archive.tar.gz>"; exit 1)
	PYTHON="$(PYTHON)" bash scripts/unpack_data.sh "$(file)" $(flags)

restore-drill:
	PYTHON="$(PYTHON)" bash scripts/restore_drill.sh "$(file)"

# ── Server ──────────────────────────────────────────────────────
server:
	$(PYTHON) -m mkb.cli api --host 127.0.0.1 --port $(API_PORT)

dev:
	PYTHON="$(PYTHON)" NPM="$(NPM)" bash scripts/dev.sh

build:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir build/wheels .

build-frontend:
	cd frontend && $(NPM) run build

# ── Tests ───────────────────────────────────────────────────────
test: test-python

lint:
	$(PYTHON) -m ruff check src tests

lint-frontend:
	cd frontend && $(NPM) run lint

test-python:
	$(PYTHON) -m pytest $(CORE_TESTS)

test-app:
	$(PYTHON) -m pytest

test-frontend: build-frontend

check: lint test-python

ci: check
