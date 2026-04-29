#!/usr/bin/env bash
# unpack_data.sh — restore PostgreSQL + MinIO + local data from a pack_data archive
# Usage:
#   bash scripts/unpack_data.sh mkb_data_20260429_120000.tar.gz
#
# Flags:
#   --pg-only      Restore only the PostgreSQL database
#   --minio-only   Restore only MinIO buckets
#   --local-only   Restore only local data directories
#   --no-pg        Skip PostgreSQL restore
#   --no-minio     Skip MinIO restore
#   --no-local     Skip local data restore
set -euo pipefail

# ── Parse args ────────────────────────────────────────────────────────────────
ARCHIVE_FILE=""
DO_PG=true
DO_MINIO=true
DO_LOCAL=true

for arg in "$@"; do
    case "$arg" in
        --pg-only)    DO_MINIO=false; DO_LOCAL=false ;;
        --minio-only) DO_PG=false;    DO_LOCAL=false ;;
        --local-only) DO_PG=false;    DO_MINIO=false ;;
        --no-pg)      DO_PG=false ;;
        --no-minio)   DO_MINIO=false ;;
        --no-local)   DO_LOCAL=false ;;
        --*)          echo "[unpack] Unknown flag: $arg" >&2; exit 1 ;;
        *)            ARCHIVE_FILE="$arg" ;;
    esac
done

if [ -z "$ARCHIVE_FILE" ]; then
    echo "Usage: bash scripts/unpack_data.sh <archive.tar.gz> [--no-pg] [--no-minio] [--no-local]"
    exit 1
fi

if [ ! -f "$ARCHIVE_FILE" ]; then
    echo "[unpack] ERROR: Archive not found: $ARCHIVE_FILE" >&2
    exit 1
fi

# ── Config (override via environment variables) ───────────────────────────────
PG_USER="${MKB_PG_USER:-mkb}"
PG_DATABASE="${MKB_PG_DATABASE:-mkb}"

MINIO_ENDPOINT="${MKB_S3_ENDPOINT:-http://localhost:9000}"
MINIO_ACCESS_KEY="${MKB_S3_ACCESS_KEY:-minioadmin}"
MINIO_SECRET_KEY="${MKB_S3_SECRET_KEY:-minioadmin}"

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo "[unpack] $*"; }
error() { echo "[unpack] ERROR: $*" >&2; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || { error "'$1' not found. $2"; exit 1; }
}

require_cmd docker "Install Docker."
require_cmd tar    "Install tar."

# ── Check Docker Compose services ─────────────────────────────────────────────
if $DO_PG; then
    if ! docker compose ps postgres 2>/dev/null | grep -q "Up\|running"; then
        error "postgres container is not running. Run 'make up' first."
        exit 1
    fi
fi

if $DO_MINIO; then
    if ! docker compose ps minio 2>/dev/null | grep -q "Up\|running"; then
        error "minio container is not running. Run 'make up' first."
        exit 1
    fi
fi

# ── Extract archive ───────────────────────────────────────────────────────────
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT

info "Extracting $ARCHIVE_FILE…"
tar -xzf "$ARCHIVE_FILE" -C "$STAGING"

# Read manifest if present
if [ -f "$STAGING/manifest.json" ]; then
    info "Archive manifest:"
    python3 -c "
import json, sys
m = json.load(open('$STAGING/manifest.json'))
print(f\"  Created at : {m.get('created_at', 'unknown')}\")
print(f\"  PG database: {m.get('pg_database', 'unknown')}\")
print(f\"  Buckets    : {', '.join(m.get('minio_buckets', []))}\")
"
fi

# ── 1. PostgreSQL restore ─────────────────────────────────────────────────────
if $DO_PG; then
    DUMP_FILE="$STAGING/postgres/dump.sql"
    if [ ! -f "$DUMP_FILE" ]; then
        error "postgres/dump.sql not found in archive."
        exit 1
    fi

    info "Restoring PostgreSQL database '$PG_DATABASE'…"
    info "  WARNING: This will DROP and recreate all tables in '$PG_DATABASE'."
    echo -n "[unpack] Continue? [y/N] "
    read -r confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        info "Skipping PostgreSQL restore."
    else
        docker compose exec -T postgres \
            psql \
            --username="$PG_USER" \
            --dbname="$PG_DATABASE" \
            --quiet \
            < "$DUMP_FILE"
        info "  PostgreSQL restore complete."
    fi
fi

# ── 2. MinIO restore ──────────────────────────────────────────────────────────
if $DO_MINIO; then
    MINIO_STAGING="$STAGING/minio"
    if [ ! -d "$MINIO_STAGING" ]; then
        error "minio/ directory not found in archive."
        exit 1
    fi

    info "Restoring MinIO buckets…"

    for bucket_dir in "$MINIO_STAGING"/*/; do
        bucket=$(basename "$bucket_dir")
        count=$(find "$bucket_dir" -type f | wc -l)
        info "  Uploading bucket '$bucket'  ($count files)…"

        docker run --rm \
            --network host \
            -v "$bucket_dir:/minio_mirror:ro" \
            minio/mc:latest \
            /bin/sh -c "
                mc alias set mkb '$MINIO_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' --api s3v4 >/dev/null 2>&1 && \
                mc mb --ignore-existing mkb/$bucket >/dev/null 2>&1 && \
                mc mirror --overwrite /minio_mirror/ mkb/$bucket 2>&1 || true
            "

        info "  → bucket '$bucket' restored."
    done
fi

# ── 3. Local data directories ─────────────────────────────────────────────────
if $DO_LOCAL; then
    LOCAL_STAGING="$STAGING/local"
    if [ ! -d "$LOCAL_STAGING" ]; then
        info "No local/ directory in archive, skipping."
    else
        info "Restoring local data directories…"

        # Walk every dir that was packed (e.g. local/data/papers, local/data/processed…)
        find "$LOCAL_STAGING" -mindepth 1 -maxdepth 3 -type d | while read -r src_dir; do
            # Compute relative path from LOCAL_STAGING
            rel_path="${src_dir#$LOCAL_STAGING/}"
            dest_dir="$rel_path"

            # Only restore dirs that have files directly under them
            file_count=$(find "$src_dir" -maxdepth 1 -type f | wc -l)
            if [ "$file_count" -gt 0 ]; then
                mkdir -p "$dest_dir"
                cp -r "$src_dir"/. "$dest_dir/"
            fi
        done

        # Top-level copy: restore the whole local/ tree onto the workspace root
        (cd "$LOCAL_STAGING" && find . -type f -print0 | tar --null -cf - --files-from -) | \
            tar xf - --keep-newer-files 2>/dev/null || true

        info "  Local data restored."
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
info ""
info "Unpack complete."
if $DO_PG; then
    info "  Run 'alembic upgrade head' if the schema migration level differs."
fi
