#!/usr/bin/env bash
# Full disposable snapshot drill: PostgreSQL, MinIO, local files, and SDK checks.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
MC_IMAGE="minio/mc:RELEASE.2025-04-16T18-13-26Z"
MINIO_IMAGE="minio/minio:RELEASE.2025-04-22T22-12-26Z"

if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
fi

PG_USER="${MKB_PG_USER:-mkb}"
PG_PORT="${MKB_PG_PORT:-5432}"
RESTORE_DB="mkb_restore_drill_$$"
MINIO_CONTAINER="mkb-restore-drill-minio-$$"
DRILL_NETWORK="mkb-restore-drill-$$"
MINIO_ACCESS_KEY="${MKB_S3_ACCESS_KEY:-minioadmin}"
MINIO_SECRET_KEY="${MKB_S3_SECRET_KEY:-minioadmin}"
DRILL_DIR=$(mktemp -d)
EVIDENCE_DIR="${MKB_RESTORE_DRILL_OUT:-$DRILL_DIR/evidence}"
mkdir -p "$EVIDENCE_DIR"
EVIDENCE_DIR=$(realpath "$EVIDENCE_DIR")

cleanup_drill() {
    docker rm -f "$MINIO_CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$DRILL_NETWORK" >/dev/null 2>&1 || true
    docker compose --project-directory "$ROOT" --file "$ROOT/docker-compose.yaml" \
        exec -T postgres \
        dropdb --username="$PG_USER" --if-exists "$RESTORE_DB" \
        >/dev/null 2>&1 || true
    rm -rf "$DRILL_DIR"
}
trap cleanup_drill EXIT

BASELINE_SOURCE="${MKB_RESTORE_DRILL_BASELINE:-}"
if [ -n "$BASELINE_SOURCE" ]; then
    if [ ! -f "$BASELINE_SOURCE" ]; then
        echo "[restore-drill] Baseline inventory not found: $BASELINE_SOURCE" >&2
        exit 1
    fi
    BASELINE_SOURCE=$(realpath "$BASELINE_SOURCE")
fi

if [ "$#" -gt 1 ]; then
    echo "Usage: bash scripts/restore_drill.sh [snapshot.tar.gz]" >&2
    exit 2
fi

if [ "$#" -eq 1 ]; then
    if [ ! -f "$1" ]; then
        echo "[restore-drill] Snapshot not found: $1" >&2
        exit 1
    fi
    ARCHIVE=$(realpath "$1")
else
    ARCHIVE="$DRILL_DIR/mkb_restore_drill.tar.gz"
fi

if [ -z "$BASELINE_SOURCE" ]; then
    echo "[restore-drill] Recording the live read-only baseline inventory…"
    (
        cd "$ROOT"
        PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
            "$PYTHON" -m mkb.cli inventory \
            --object-checksums \
            --out "$EVIDENCE_DIR/baseline-inventory.json"
    )
else
    echo "[restore-drill] Using saved historical baseline inventory…"
fi

if [ "$#" -eq 0 ]; then
    echo "[restore-drill] Creating snapshot…"
    (cd "$ROOT" && bash "$SCRIPT_DIR/pack_data.sh" "$ARCHIVE")
fi

EXTRACTED="$DRILL_DIR/extracted"
echo "[restore-drill] Validating checksums and safely extracting snapshot…"
"$PYTHON" "$SCRIPT_DIR/snapshot_manifest.py" extract "$ARCHIVE" "$EXTRACTED"
cp "$EXTRACTED/manifest.json" "$EVIDENCE_DIR/snapshot-manifest.json"
if [ -n "$BASELINE_SOURCE" ]; then
    "$PYTHON" "$SCRIPT_DIR/snapshot_manifest.py" enrich-inventory \
        "$BASELINE_SOURCE" "$EXTRACTED/manifest.json" \
        "$EVIDENCE_DIR/baseline-inventory.json"
fi

echo "[restore-drill] Restoring PostgreSQL into disposable database '$RESTORE_DB'…"
docker compose --project-directory "$ROOT" --file "$ROOT/docker-compose.yaml" \
    exec -T postgres \
    createdb --username="$PG_USER" "$RESTORE_DB"
docker compose --project-directory "$ROOT" --file "$ROOT/docker-compose.yaml" \
    exec -T postgres \
    psql --username="$PG_USER" --dbname="$RESTORE_DB" \
    --set=ON_ERROR_STOP=1 --quiet < "$EXTRACTED/postgres/dump.sql"

echo "[restore-drill] Starting disposable MinIO container '$MINIO_CONTAINER'…"
docker network create "$DRILL_NETWORK" >/dev/null
docker run --detach --name "$MINIO_CONTAINER" \
    --network "$DRILL_NETWORK" \
    --publish 127.0.0.1::9000 \
    --env "MINIO_ROOT_USER=$MINIO_ACCESS_KEY" \
    --env "MINIO_ROOT_PASSWORD=$MINIO_SECRET_KEY" \
    "$MINIO_IMAGE" server /data >/dev/null

ready=false
for _attempt in $(seq 1 60); do
    if docker exec "$MINIO_CONTAINER" mc ready local >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
if ! $ready; then
    echo "[restore-drill] Disposable MinIO did not become ready." >&2
    exit 1
fi

MINIO_PORT=$(docker port "$MINIO_CONTAINER" 9000/tcp | sed -E 's/.*:([0-9]+)$/\1/' | head -n 1)
MINIO_ENDPOINT="http://127.0.0.1:$MINIO_PORT"
MINIO_INTERNAL_ENDPOINT="http://$MINIO_CONTAINER:9000"
echo "[restore-drill] Restoring all four buckets into disposable MinIO…"
docker run --rm --network "$DRILL_NETWORK" \
    --volume "$EXTRACTED/minio:/snapshot:ro" \
    --env MC_CONFIG_DIR=/tmp/.mc \
    --entrypoint /bin/sh \
    "$MC_IMAGE" -c "
        set -eu
        mc alias set drill '$MINIO_INTERNAL_ENDPOINT' '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' --api s3v4 >/dev/null
        for bucket in raw processed archive temp; do
            mc mb --ignore-existing drill/\"\$bucket\" >/dev/null
            mc mirror --overwrite /snapshot/\"\$bucket\"/ drill/\"\$bucket\"/ >/dev/null
        done
    "

RESTORED_ROOT="$DRILL_DIR/restored-local"
mkdir -p "$RESTORED_ROOT"
echo "[restore-drill] Restoring local files into temporary root…"
(
    cd "$EXTRACTED/local"
    find . -type f -print0 | tar --null -cf - --files-from -
) | (
    cd "$RESTORED_ROOT"
    tar xf -
)

echo "[restore-drill] Running SDK inventory and reconciliation on restored copies…"
(
    cd "$RESTORED_ROOT"
    export MKB_PG_HOST=127.0.0.1
    export MKB_PG_PORT="$PG_PORT"
    export MKB_PG_DATABASE="$RESTORE_DB"
    export MKB_S3_ENDPOINT="$MINIO_ENDPOINT"
    export MKB_S3_ACCESS_KEY="$MINIO_ACCESS_KEY"
    export MKB_S3_SECRET_KEY="$MINIO_SECRET_KEY"
    export MKB_PROCESSED_LOCAL_ROOT=data/processed
    export MKB_RUNTIME_SETTINGS_PATH=data/runtime_settings.json
    export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

    "$PYTHON" -m mkb.cli inventory \
        --object-checksums \
        --out "$EVIDENCE_DIR/restored-inventory.json"
    "$PYTHON" -m mkb.cli migration-preflight \
        "$EVIDENCE_DIR/baseline-inventory.json" \
        "$EVIDENCE_DIR/restored-inventory.json" \
        --out "$EVIDENCE_DIR/inventory-comparison.json"
    "$PYTHON" -m mkb.cli reconcile --summary \
        --out "$EVIDENCE_DIR/reconciliation.json"
    "$PYTHON" -m mkb.cli verify-content --sample-size 10 \
        --out "$EVIDENCE_DIR/content-verification.json"
)

echo "[restore-drill] Full disposable restore and SDK validation passed."
echo "[restore-drill] Evidence: $EVIDENCE_DIR"
