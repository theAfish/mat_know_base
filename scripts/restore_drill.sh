#!/usr/bin/env bash
# Validate a snapshot against an externally managed PostgreSQL server.
# Creates and removes only a disposable validation database; live data is untouched.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Usage: bash scripts/restore_drill.sh <snapshot.tar.gz>" >&2
    exit 2
fi

# unpack validates checksums, restores SQL into a disposable database, and checks
# its object references against archived S3/filesystem payloads. No object store
# service needs to be started or overwritten for this validation.
exec bash "$SCRIPT_DIR/unpack_data.sh" "$1" --validate-only
