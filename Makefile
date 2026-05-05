.PHONY: up down logs migrate ingest list batches info purge install test

# ── Infrastructure ──────────────────────────────────────────────
up:
	docker compose up -d
	@echo "Waiting for services…"
	@docker compose exec postgres pg_isready -U mkb -q && echo "PostgreSQL ready" || true
	@echo "MinIO console: http://localhost:9001  (minioadmin / minioadmin)"

down:
	docker compose down

logs:
	docker compose logs -f

# ── Database ────────────────────────────────────────────────────
migrate:
	alembic upgrade head

migration:  ## usage: make migration msg="add foo table"
	alembic revision --autogenerate -m "$(msg)"

# ── Python ──────────────────────────────────────────────────────
install:
	pip install -e ".[dev]"

# ── CLI shortcuts ───────────────────────────────────────────────
ingest:  ## usage: make ingest dir=./data/inbox
	python -m mkb.cli ingest $(dir)

list:
	python -m mkb.cli list

batches:
	python -m mkb.cli batches

info:  ## usage: make info id=<asset_id or sha256_prefix>
	python -m mkb.cli info $(id)

purge:
	python -m mkb.cli purge

# ── Data sharing ────────────────────────────────────────────────
pack:  ## Create a portable snapshot: make pack [out=my_snapshot.tar.gz]
	bash scripts/pack_data.sh $(if $(out),$(out),)

unpack:  ## Restore from snapshot: make unpack file=mkb_data_YYYYMMDD.tar.gz
	@[ -n "$(file)" ] || (echo "Usage: make unpack file=<archive.tar.gz>"; exit 1)
	bash scripts/unpack_data.sh $(file)

# ── Server ──────────────────────────────────────────────────────
server:
	python -m mkb.cli api --host 127.0.0.1 --port 8503

# ── Tests ───────────────────────────────────────────────────────
test:
	pytest tests/ -v
