#!/usr/bin/env bash
# Disposable backup/restore validation drill; safe for CI or a scheduled host job.
set -euo pipefail

DRILL_DIR=$(mktemp -d)
cleanup_drill() { rm -rf "$DRILL_DIR"; }
trap cleanup_drill EXIT

ARCHIVE="$DRILL_DIR/mkb_restore_drill.tar.gz"
bash scripts/pack_data.sh "$ARCHIVE"
set +e
bash scripts/unpack_data.sh "$ARCHIVE"
status=$?
set -e
if [ "$status" -ne 2 ]; then
    echo "[restore-drill] Expected validation-only exit 2; got $status" >&2
    exit 1
fi
echo "[restore-drill] Snapshot and disposable database restore validation passed."
