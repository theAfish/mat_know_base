#!/usr/bin/env bash
# Shared libpq setup for the optional legacy PostgreSQL snapshot tools.
# The caller loads snapshot_payload.py settings before sourcing this file.

snapshot_postgres() {
    local client
    for client in "$@"; do
        if ! command -v "$client" >/dev/null 2>&1; then
            echo "[snapshot] Missing PostgreSQL client '$client'; install PostgreSQL client tools." >&2
            return 1
        fi
    done
    # Match the application's resolved MKB_PG_* settings. Keep passwords out of
    # command arguments, and preserve libpq options such as PGSSLMODE/PGPASSFILE.
    export PGHOST="$PG_HOST" PGPORT="$PG_PORT" PGUSER="$PG_USER"
    export PGPASSWORD="$PG_PASSWORD"
    export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-10}"
}
