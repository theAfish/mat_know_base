#!/usr/bin/env bash
# Complete business data snapshot, excluding application settings and credentials.
# Usage: bash scripts/pack_data.sh [snapshot.tar.gz]
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
WORKSPACE_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON="${PYTHON:-$WORKSPACE_ROOT/.venv/bin/python}"
ARCHIVE_NAME=$(realpath -m "${1:-mkb_data_$(date +%Y%m%d_%H%M%S).tar.gz}")
if [ "$#" -gt 1 ] || [ -e "$ARCHIVE_NAME" ]; then
    echo "[pack] Expected one new archive path; refusing to overwrite an existing file." >&2
    exit 1
fi
cd "$WORKSPACE_ROOT"
export PYTHONPATH="$WORKSPACE_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
# Use the same environment/.env/config.yaml precedence as the application.
CONFIG=$("$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" settings)
eval "$CONFIG"
"$PYTHON" - "$ARCHIVE_NAME" <<'PY'
import sys
from pathlib import Path
from mkb.config import settings
from scripts.snapshot_payload import local_paths
archive = Path(sys.argv[1])
for relative in local_paths(settings, Path.cwd()):
    source = (Path.cwd() / relative).resolve()
    if archive == source or source in archive.parents:
        raise SystemExit("[pack] Save the archive outside the data directories being backed up.")
PY

echo "[pack] Checking business file references ($OBJECT_STORE_BACKEND backend)…"
"$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" verify

STAGING=$(mktemp -d)
PARTIAL="${ARCHIVE_NAME}.partial.$$"
trap 'rm -rf "$STAGING"; rm -f "$PARTIAL"' EXIT
mkdir -p "$STAGING/postgres" "$(dirname "$ARCHIVE_NAME")"
echo "[pack] Dumping PostgreSQL database '$PG_DATABASE'…"
docker compose exec -T postgres pg_dump \
    --username="$PG_USER" --clean --if-exists --no-owner --no-acl \
    "$PG_DATABASE" > "$STAGING/postgres/dump.sql"

if [ "$OBJECT_STORE_BACKEND" = s3 ]; then
    echo "[pack] Copying S3 objects…"
    "$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" backup-s3 "$STAGING/minio"
fi
echo "[pack] Copying all local business data (settings excluded)…"
"$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" stage "$STAGING"
echo "[pack] Checking staged files against database references…"
"$PYTHON" "$SCRIPT_DIR/snapshot_payload.py" verify "$STAGING"
echo "[pack] Writing SHA-256 manifest…"
"$PYTHON" "$SCRIPT_DIR/snapshot_manifest.py" create "$STAGING" --revision legacy-schema-retired
echo "[pack] Compressing archive…"
tar -czf "$PARTIAL" -C "$STAGING" .
mv -n "$PARTIAL" "$ARCHIVE_NAME"
if [ -e "$PARTIAL" ]; then
    echo "[pack] Another process created the output; refusing to overwrite it." >&2
    exit 1
fi
echo "[pack] Done: $ARCHIVE_NAME ($(du -h "$ARCHIVE_NAME" | cut -f1))"
if [ -n "${MKB_SNAPSHOT_AGE_RECIPIENT:-}" ]; then
    age --recipient "$MKB_SNAPSHOT_AGE_RECIPIENT" --output "${ARCHIVE_NAME}.age" "$ARCHIVE_NAME"
    echo "[pack] Encrypted copy: ${ARCHIVE_NAME}.age (plaintext retained)"
fi
echo "[pack] Validate with: bash scripts/unpack_data.sh '$ARCHIVE_NAME' --validate-only"
