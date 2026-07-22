.PHONY: setup bootstrap install install-python install-frontend up down logs doctor ingest list batches info purge test lint test-python test-frontend build dev server check ci cleanup reconcile pack unpack restore-drill

PYTHON ?= .venv/bin/python
BOOTSTRAP_PYTHON ?= python3
NPM ?= npm
export PYTHONPATH := $(CURDIR)/src$(if $(PYTHONPATH),:$(PYTHONPATH))

# ── Clean-clone bootstrap ──────────────────────────────────────
setup: bootstrap

bootstrap:
	$(BOOTSTRAP_PYTHON) -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(MAKE) install
	@test -f .env || cp .env.example .env
	@echo "Bootstrap complete. Review .env, then run 'make up' and 'make dev'."

install: install-python install-frontend

install-python:
	$(PYTHON) -m pip install -e ".[all,dev]"

install-frontend:
	cd frontend && $(NPM) ci

# ── Infrastructure ──────────────────────────────────────────────
up:
	docker compose up -d --wait
	@echo "MKB data services are healthy."

down:
	docker compose down

logs:
	docker compose logs -f

doctor:
	$(PYTHON) -m mkb.doctor

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
	bash scripts/pack_data.sh $(if $(out),$(out),)

unpack:  ## Restore from snapshot: make unpack file=mkb_data_YYYYMMDD.tar.gz
	@[ -n "$(file)" ] || (echo "Usage: make unpack file=<archive.tar.gz>"; exit 1)
	bash scripts/unpack_data.sh $(file)

restore-drill:
	bash scripts/restore_drill.sh $(if $(file),$(file),)

# ── Server ──────────────────────────────────────────────────────
server:
	$(PYTHON) -m mkb.cli api --host 127.0.0.1 --port 8503

dev:
	PYTHON="$(PYTHON)" NPM="$(NPM)" bash scripts/dev.sh

build:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir build/wheels .
	cd frontend && $(NPM) run build

# ── Tests ───────────────────────────────────────────────────────
test:
	$(PYTHON) -m pytest tests/ -v

lint:
	$(PYTHON) -m ruff check src tests
	cd frontend && $(NPM) run lint

test-python:
	$(PYTHON) -m pytest

test-frontend:
	cd frontend && $(NPM) run build

check: lint test-python test-frontend

ci:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m pytest --collect-only -q
