#!/usr/bin/env bash
# Full disposable snapshot drill: PostgreSQL, object storage, local files, and SDK checks.
#
# Under the default "sql" object-store backend the object bytes are restored by the
# PostgreSQL dump itself, so the drill needs no second storage service. The "s3"
# backend still spins up a disposable MinIO to restore the mirrored buckets into.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
MINIO_IMAGE="${MKB_RESTORE_DRILL_MINIO_IMAGE:-minio/minio:RELEASE.2025-04-22T22-12-26Z}"
CONFIG=$(cd "$ROOT" && PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" settings)
eval "$CONFIG"
export MKB_OBJECT_STORE_BACKEND="$OBJECT_STORE_BACKEND"
RESTORE_DB="mkb_restore_drill_$$"
MINIO_CONTAINER="mkb-restore-drill-minio-$$"
DRILL_NETWORK="mkb-restore-drill-$$"
MINIO_ACCESS_KEY="drill-access"
MINIO_SECRET_KEY="drill-secret-$RANDOM-$RANDOM"
DRILL_DIR=$(mktemp -d)
EVIDENCE_DIR="${MKB_RESTORE_DRILL_OUT:-$DRILL_DIR/evidence}"
mkdir -p "$EVIDENCE_DIR"
EVIDENCE_DIR=$(realpath "$EVIDENCE_DIR")

cleanup_drill() {
    docker rm -fv "$MINIO_CONTAINER" >/dev/null 2>&1 || true
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
"$PYTHON" - "$EXTRACTED" "$OBJECT_STORE_BACKEND" <<'CHECK'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
metadata = root / "snapshot.json"
backend = json.loads(metadata.read_text())["object_store_backend"] if metadata.exists() else ("s3" if (root / "minio").is_dir() else "sql")
if backend != sys.argv[2]:
    raise SystemExit(f"Set MKB_OBJECT_STORE_BACKEND={backend} for this restore drill")
CHECK
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

if [ "$OBJECT_STORE_BACKEND" = "s3" ]; then
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
    echo "[restore-drill] Restoring archived buckets into disposable MinIO…"
    (
        cd "$ROOT"
        MKB_S3_ENDPOINT="$MINIO_ENDPOINT" \
        MKB_S3_ACCESS_KEY="$MINIO_ACCESS_KEY" \
        MKB_S3_SECRET_KEY="$MINIO_SECRET_KEY" \
        PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
            "$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" restore-s3 "$EXTRACTED/minio"
    )
elif [ "$OBJECT_STORE_BACKEND" = sql ]; then
    echo "[restore-drill] SQL objects restored with the database."
else
    echo "[restore-drill] File objects will be restored with local business files."
fi

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
    export MKB_PG_USER="$PG_USER"
    export MKB_PG_PASSWORD="$PG_PASSWORD"
    export MKB_PG_PORT="$PG_PORT"
    export MKB_PG_DATABASE="$RESTORE_DB"
    export MKB_OBJECT_STORE_BACKEND="$OBJECT_STORE_BACKEND"
    export MKB_S3_BUCKET_RAW="$BUCKET_RAW"
    export MKB_S3_BUCKET_PROCESSED="$BUCKET_PROCESSED"
    export MKB_S3_BUCKET_ARCHIVE="$BUCKET_ARCHIVE"
    export MKB_S3_BUCKET_TEMP="$BUCKET_TEMP"
    if [ "$OBJECT_STORE_BACKEND" = "s3" ]; then
        export MKB_S3_ENDPOINT="$MINIO_ENDPOINT"
        export MKB_S3_ACCESS_KEY="$MINIO_ACCESS_KEY"
        export MKB_S3_SECRET_KEY="$MINIO_SECRET_KEY"
    fi
    export MKB_PROCESSED_LOCAL_ROOT="$PROCESSED_LOCAL_ROOT"
    export MKB_OBJECT_STORE_ROOT="$OBJECT_STORE_ROOT"
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
