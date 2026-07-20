# MKB operator runbook

MKB defaults to loopback-only operation. Before startup, run `make up`, `make
migrate`, and `curl http://127.0.0.1:8503/health/ready`. Liveness only confirms
the API process; readiness categorizes database, schema, object-storage, and
worker failures without returning credentials.

For a clean shutdown, stop accepting work, check `/api/jobs` for active jobs,
cancel them through the API, wait for terminal states, then stop the API and run
`make down`. A crash or forced restart marks leftover jobs `INTERRUPTED`; inspect
their `request_id`, events, and error category before retrying the originating
action. Active-key constraints prevent duplicate work across API processes.

For stuck work, query the job, request cooperative cancellation, and inspect the
request ID in rotating logs. Do not kill worker threads inside a live process.
Provider outages should produce a failed/interrupted durable record; retry only
after readiness and provider checks recover.

For full disks, run `mkb cleanup` first. It is a dry run and reports item/byte
counts. Review the paths, then use `mkb cleanup --apply --confirm DELETE`.
Use `mkb reconcile` before and after cleanup; it is read-only and reports missing
and orphaned PostgreSQL/MinIO objects.

## Backup, restore, and upgrade

`make pack` fails if PostgreSQL or any required bucket cannot be copied. The
archive contains a versioned manifest, schema revision, application version,
file sizes, and SHA-256 checksums. Store/encrypt the resulting archive with your
organization's approved backup tooling.

Restore is deliberately two-step:

1. `bash scripts/unpack_data.sh snapshot.tar.gz` validates paths, types, manifest,
   and checksums in a temporary staging directory and exits without mutation.
2. Re-run with `--confirm-replace`, then type the database name when prompted.

The restore mirrors bucket contents with removal, replaces the database from the
validated dump, and restores local files. Run `make migrate`, `mkb reconcile`,
and the readiness probe afterward. Practice this against a disposable Compose
project before depending on a backup. `make restore-drill` performs the pack,
checksum/path validation, and disposable-database restore and is suitable for a
scheduled job or integration CI runner with Docker services.

Before an application upgrade, create and verify a snapshot, stop job starts,
upgrade the code, run `make migrate`, then verify readiness and reconciliation.
