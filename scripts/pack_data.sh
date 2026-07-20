#!/usr/bin/env bash
# pack_data.sh — bundle PostgreSQL + MinIO + local data dirs into a single archive
# Usage:
#   bash scripts/pack_data.sh                   # mkb_data_YYYYMMDD_HHMMSS.tar.gz
#   bash scripts/pack_data.sh my_snapshot.tar.gz  # custom output name
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE_NAME="${1:-mkb_data_${TIMESTAMP}.tar.gz}"

PG_USER="${MKB_PG_USER:-mkb}"
PG_DATABASE="${MKB_PG_DATABASE:-mkb}"

MINIO_ENDPOINT="${MKB_S3_ENDPOINT:-http://localhost:9000}"
MINIO_ACCESS_KEY="${MKB_S3_ACCESS_KEY:-minioadmin}"
MINIO_SECRET_KEY="${MKB_S3_SECRET_KEY:-minioadmin}"
MINIO_BUCKETS=(raw processed archive temp)
MC_IMAGE="minio/mc:RELEASE.2025-04-16T18-13-26Z"

LOCAL_DATA_DIRS=(data/papers data/processed data/uploads data/inbox)

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo "[pack] $*"; }
error() { echo "[pack] ERROR: $*" >&2; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || { error "'$1' not found. $2"; exit 1; }
}

require_cmd docker "Install Docker."
require_cmd tar    "Install tar (should be available on any Linux/macOS)."

# ── Check Docker Compose services ─────────────────────────────────────────────
info "Checking that postgres and minio are running…"
if ! docker compose ps postgres 2>/dev/null | grep -q "Up\|running"; then
    error "postgres container is not running. Run 'make up' first."
    exit 1
fi
if ! docker compose ps minio 2>/dev/null | grep -q "Up\|running"; then
    error "minio container is not running. Run 'make up' first."
    exit 1
fi

# ── Staging directory ─────────────────────────────────────────────────────────
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT

info "Staging to: $STAGING"

# ── 1. PostgreSQL dump ────────────────────────────────────────────────────────
info "Dumping PostgreSQL database '$PG_DATABASE'…"
mkdir -p "$STAGING/postgres"
docker compose exec -T postgres \
    pg_dump \
    --username="$PG_USER" \
    --clean \
    --if-exists \
    --no-owner \
    --no-acl \
    "$PG_DATABASE" \
    > "$STAGING/postgres/dump.sql"
info "  → postgres/dump.sql ($(wc -c < "$STAGING/postgres/dump.sql") bytes)"

# ── 2. MinIO buckets ──────────────────────────────────────────────────────────
info "Mirroring MinIO buckets…"
mkdir -p "$STAGING/minio"

for bucket in "${MINIO_BUCKETS[@]}"; do
    info "  Mirroring bucket: $bucket"
    mkdir -p "$STAGING/minio/$bucket"

    docker run --rm \
        --network host \
        -v "$STAGING/minio/$bucket:/minio_mirror" \
        --user "$(id -u):$(id -g)" \
        -e MC_CONFIG_DIR=/tmp/.mc \
        --entrypoint /bin/sh \
        "$MC_IMAGE" \
        -c "
            mc alias set mkb '$MINIO_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' --api s3v4 >/dev/null 2>&1 && \
            mc stat mkb/$bucket >/dev/null && \
            mc mirror --overwrite mkb/$bucket /minio_mirror/
        "

    count=$(find "$STAGING/minio/$bucket" -type f | wc -l)
    info "  → minio/$bucket  ($count files)"
done

# ── 3. Local data directories ─────────────────────────────────────────────────
info "Copying local data directories…"
mkdir -p "$STAGING/local"

for dir in "${LOCAL_DATA_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        dest="$STAGING/local/$dir"
        mkdir -p "$dest"
        # Use find+cp to avoid issues with empty dirs or non-rsync environments
        (cd "$dir" && find . -type f -print0 | tar --null -cf - --files-from -) | \
            (mkdir -p "$dest" && cd "$dest" && tar xf -)
        count=$(find "$dest" -type f | wc -l)
        info "  → local/$dir  ($count files)"
    else
        info "  (skipping '$dir' — does not exist)"
    fi
done

# ── 4. Manifest ───────────────────────────────────────────────────────────────
info "Writing checksummed manifest…"
SCHEMA_REVISION=$(docker compose exec -T postgres psql --username="$PG_USER" --dbname="$PG_DATABASE" --tuples-only --no-align --command="SELECT version_num FROM alembic_version")
python3 scripts/snapshot_manifest.py create "$STAGING" --revision "$SCHEMA_REVISION"

# ── 5. Create archive ─────────────────────────────────────────────────────────
info "Creating archive: $ARCHIVE_NAME"
tar -czf "$ARCHIVE_NAME" -C "$STAGING" .

SIZE=$(du -sh "$ARCHIVE_NAME" | cut -f1)
info "Done! Archive: $ARCHIVE_NAME  ($SIZE)"
if [ -n "${MKB_SNAPSHOT_AGE_RECIPIENT:-}" ]; then
    require_cmd age "Install age to encrypt snapshots."
    age --recipient "$MKB_SNAPSHOT_AGE_RECIPIENT" --output "${ARCHIVE_NAME}.age" "$ARCHIVE_NAME"
    info "Encrypted copy: ${ARCHIVE_NAME}.age (plaintext retained for explicit handling)"
fi
info ""
info "Share this file and have others run:"
info "  bash scripts/unpack_data.sh $ARCHIVE_NAME"
