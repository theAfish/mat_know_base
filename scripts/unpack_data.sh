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

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
WORKSPACE_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)

# ── Parse args ────────────────────────────────────────────────────────────────
ARCHIVE_FILE=""
DO_PG=true
DO_MINIO=true
DO_LOCAL=true
CONFIRM_REPLACE=false

for arg in "$@"; do
    case "$arg" in
        --pg-only)    DO_MINIO=false; DO_LOCAL=false ;;
        --minio-only) DO_PG=false;    DO_LOCAL=false ;;
        --local-only) DO_PG=false;    DO_MINIO=false ;;
        --no-pg)      DO_PG=false ;;
        --no-minio)   DO_MINIO=false ;;
        --no-local)   DO_LOCAL=false ;;
        --confirm-replace) CONFIRM_REPLACE=true ;;
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
RESTORE_TEST_DB=""
cleanup_staging() {
    if [ -n "$RESTORE_TEST_DB" ]; then
        docker compose exec -T postgres dropdb --username="$PG_USER" --if-exists "$RESTORE_TEST_DB" >/dev/null 2>&1 || true
    fi
    rm -rf "$STAGING"
}
trap cleanup_staging EXIT

info "Validating and extracting $ARCHIVE_FILE into staging…"
VALIDATION_ARCHIVE="$ARCHIVE_FILE"
if [[ "$ARCHIVE_FILE" == *.age ]]; then
    require_cmd age "Install age to decrypt snapshots."
    if [ -z "${MKB_AGE_IDENTITY:-}" ]; then
        error "Set MKB_AGE_IDENTITY to the age identity file."
        exit 1
    fi
    VALIDATION_ARCHIVE="$STAGING/decrypted.tar.gz"
    age --decrypt --identity "$MKB_AGE_IDENTITY" --output "$VALIDATION_ARCHIVE" "$ARCHIVE_FILE"
fi
EXTRACTED="$STAGING/extracted"
python3 "$SCRIPT_DIR/snapshot_manifest.py" extract "$VALIDATION_ARCHIVE" "$EXTRACTED"

if $DO_PG; then
    DUMP_FILE="$EXTRACTED/postgres/dump.sql"
    if [ ! -f "$DUMP_FILE" ]; then
        error "postgres/dump.sql not found in archive."
        exit 1
    fi
    RESTORE_TEST_DB="mkb_restore_test_$$"
    info "Restoring PostgreSQL dump into disposable staging database…"
    docker compose exec -T postgres createdb --username="$PG_USER" "$RESTORE_TEST_DB"
    docker compose exec -T postgres psql --username="$PG_USER" --dbname="$RESTORE_TEST_DB" --set=ON_ERROR_STOP=1 --quiet < "$DUMP_FILE"
    docker compose exec -T postgres dropdb --username="$PG_USER" "$RESTORE_TEST_DB"
    RESTORE_TEST_DB=""
    info "Disposable database restore validation passed."
fi

# Read manifest if present
if [ -f "$EXTRACTED/manifest.json" ]; then
    info "Archive manifest:"
    python3 -m json.tool "$EXTRACTED/manifest.json"
fi

if ! $CONFIRM_REPLACE; then
    error "Snapshot validated in staging. Re-run with --confirm-replace to replace live data."
    exit 2
fi

# ── 1. PostgreSQL restore ─────────────────────────────────────────────────────
if $DO_PG; then
    DUMP_FILE="$EXTRACTED/postgres/dump.sql"
    if [ ! -f "$DUMP_FILE" ]; then
        error "postgres/dump.sql not found in archive."
        exit 1
    fi

    info "Restoring PostgreSQL database '$PG_DATABASE'…"
    info "  WARNING: This will DROP and recreate all tables in '$PG_DATABASE'."
    echo -n "[unpack] Type the database name '$PG_DATABASE' to confirm replacement: "
    read -r confirm
    if [[ "$confirm" == "$PG_DATABASE" ]]; then
        docker compose exec -T postgres \
            psql \
            --username="$PG_USER" \
            --dbname="$PG_DATABASE" \
            --quiet \
            < "$DUMP_FILE"
        info "  PostgreSQL restore complete."
    else
        error "Database confirmation did not match."
        exit 2
    fi
fi

# ── 2. MinIO restore ──────────────────────────────────────────────────────────
if $DO_MINIO; then
    MINIO_STAGING="$EXTRACTED/minio"
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
            --user "$(id -u):$(id -g)" \
            -e MC_CONFIG_DIR=/tmp/.mc \
            --entrypoint /bin/sh \
            minio/mc:RELEASE.2025-04-16T18-13-26Z \
            -c "
                mc alias set mkb '$MINIO_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' --api s3v4 >/dev/null 2>&1 && \
                mc mb --ignore-existing mkb/$bucket >/dev/null 2>&1 && \
                mc mirror --overwrite --remove /minio_mirror/ mkb/$bucket >/dev/null
            "

        info "  → bucket '$bucket' restored."
    done
fi

# ── 3. Local data directories ─────────────────────────────────────────────────
if $DO_LOCAL; then
    LOCAL_STAGING="$EXTRACTED/local"
    if [ ! -d "$LOCAL_STAGING" ]; then
        info "No local/ directory in archive, skipping."
    else
        info "Restoring local data directories…"
        # Restore the whole local/ tree onto the workspace root, independent of cwd.
        (cd "$LOCAL_STAGING" && find . -type f -print0 | tar --null -cf - --files-from -) | \
            tar xf - -C "$WORKSPACE_ROOT"

        info "  Local data restored."
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
info ""
info "Unpack complete."
