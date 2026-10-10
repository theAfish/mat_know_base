#!/usr/bin/env bash
# Validate business snapshots, then explicitly opt in to live replacement.
# Usage: bash scripts/unpack_data.sh snapshot.tar.gz [--validate-only]
# Restore: add --confirm-replace; database replacement also asks for its name.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
WORKSPACE_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON="${PYTHON:-$WORKSPACE_ROOT/.venv/bin/python}"
ARCHIVE_FILE=""
DO_PG=true
DO_BUCKETS=auto
DO_LOCAL=true
CONFIRM_REPLACE=false
VALIDATE_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --pg-only) DO_BUCKETS=false; DO_LOCAL=false ;;
        --buckets-only) DO_BUCKETS=true; DO_PG=false; DO_LOCAL=false ;;
        --local-only) DO_PG=false; DO_BUCKETS=false ;;
        --no-pg) DO_PG=false ;;
        --no-buckets) DO_BUCKETS=false ;;
        --no-local) DO_LOCAL=false ;;
        --validate-only) VALIDATE_ONLY=true ;;
        --confirm-replace) CONFIRM_REPLACE=true ;;
        --*) echo "[unpack] Unknown flag: $arg" >&2; exit 1 ;;
        *)
            if [ -n "$ARCHIVE_FILE" ]; then echo "[unpack] Expected one archive." >&2; exit 1; fi
            ARCHIVE_FILE="$arg" ;;
    esac
done
if [ -z "$ARCHIVE_FILE" ] || [ ! -f "$ARCHIVE_FILE" ]; then
    echo "Usage: bash scripts/unpack_data.sh <archive.tar.gz> [--validate-only | --confirm-replace]" >&2
    exit 1
fi
ARCHIVE_FILE=$(realpath "$ARCHIVE_FILE")
cd "$WORKSPACE_ROOT"
export PYTHONPATH="$WORKSPACE_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
CONFIG=$("$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" settings)
eval "$CONFIG"
STAGING=$(mktemp -d)
RESTORE_TEST_DB=""
cleanup_staging() {
    if [ -n "$RESTORE_TEST_DB" ]; then
        docker compose exec -T postgres dropdb --username="$PG_USER" --if-exists "$RESTORE_TEST_DB" >/dev/null 2>&1 || true
    fi
    rm -rf "$STAGING"
}
trap cleanup_staging EXIT

VALIDATION_ARCHIVE="$ARCHIVE_FILE"
if [[ "$ARCHIVE_FILE" == *.age ]]; then
    : "${MKB_AGE_IDENTITY:?Set MKB_AGE_IDENTITY to decrypt snapshots}"
    VALIDATION_ARCHIVE="$STAGING/decrypted.tar.gz"
    age --decrypt --identity "$MKB_AGE_IDENTITY" --output "$VALIDATION_ARCHIVE" "$ARCHIVE_FILE"
fi
EXTRACTED="$STAGING/extracted"
echo "[unpack] Extracting and checking all snapshot checksums…"
"$PYTHON" "$SCRIPT_DIR/snapshot_manifest.py" extract "$VALIDATION_ARCHIVE" "$EXTRACTED"
ARCHIVE_BACKEND=$("$PYTHON" - "$EXTRACTED" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
metadata = root / 'snapshot.json'
backend = json.loads(metadata.read_text())['object_store_backend'] if metadata.exists() else ('s3' if (root / 'minio').is_dir() else 'sql')
if backend not in {'s3', 'sql', 'file'}:
    raise SystemExit('Unsupported snapshot storage backend')
print(backend)
PY
)
echo "[unpack] Snapshot storage backend: $ARCHIVE_BACKEND"
if [ "$DO_BUCKETS" = auto ]; then
    if [ "$ARCHIVE_BACKEND" = s3 ]; then DO_BUCKETS=true; else DO_BUCKETS=false; fi
fi
if $DO_BUCKETS && [ ! -d "$EXTRACTED/minio" ]; then
    echo "[unpack] Missing minio/ payload." >&2
    exit 1
fi
if $DO_LOCAL; then
    "$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" check-local "$EXTRACTED/local"
fi
if $DO_PG; then
    DUMP_FILE="$EXTRACTED/postgres/dump.sql"
    test -s "$DUMP_FILE"
    RESTORE_TEST_DB="mkb_restore_test_$$"
    echo "[unpack] Restoring dump into disposable database '$RESTORE_TEST_DB'…"
    docker compose exec -T postgres createdb --username="$PG_USER" "$RESTORE_TEST_DB"
    docker compose exec -T postgres psql --no-psqlrc --username="$PG_USER" \
        --dbname="$RESTORE_TEST_DB" --set=ON_ERROR_STOP=1 --quiet < "$DUMP_FILE"
    MKB_OBJECT_STORE_BACKEND="$ARCHIVE_BACKEND" "$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" \
        verify "$EXTRACTED" --database "$RESTORE_TEST_DB"
    docker compose exec -T postgres dropdb --username="$PG_USER" "$RESTORE_TEST_DB"
    RESTORE_TEST_DB=""
    echo "[unpack] Disposable database restore and business file validation passed."
fi
if $VALIDATE_ONLY; then
    echo "[unpack] Validation passed; live data unchanged."
    exit 0
fi
if ! $CONFIRM_REPLACE; then
    echo "[unpack] Validated. Add --confirm-replace to replace live data." >&2
    exit 2
fi
# Never silently skip an archived S3 payload because a new installation defaults
# to SQL. Settings are intentionally not part of the business snapshot.
if { $DO_PG || $DO_BUCKETS; } && [ "$ARCHIVE_BACKEND" != "$OBJECT_STORE_BACKEND" ]; then
    echo "[unpack] Configure MKB_OBJECT_STORE_BACKEND=$ARCHIVE_BACKEND on the target first." >&2
    exit 1
fi
if $DO_PG; then
    echo "[unpack] This replaces tables in '$PG_DATABASE'."
    read -r -p "[unpack] Type database name '$PG_DATABASE' to confirm: " confirm
    if [ "$confirm" != "$PG_DATABASE" ]; then exit 2; fi
    docker compose exec -T postgres psql --no-psqlrc --username="$PG_USER" \
        --dbname="$PG_DATABASE" --set=ON_ERROR_STOP=1 --single-transaction --quiet < "$DUMP_FILE"
fi
if $DO_BUCKETS; then
    echo "[unpack] Restoring S3 buckets…"
    "$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" restore-s3 "$EXTRACTED/minio"
fi
if $DO_LOCAL; then
    echo "[unpack] Restoring local business files…"
    "$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" restore-local "$EXTRACTED/local"
fi
echo "[unpack] Restore complete. Application settings were preserved."
