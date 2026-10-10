# MKB operator runbook

MKB defaults to loopback-only operation. Before startup, run `make up` and
`curl http://127.0.0.1:8503/health/ready`. Liveness only confirms
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
and orphaned PostgreSQL/object-store objects.

## Backup, restore, and upgrade

`make pack` fails if PostgreSQL or any required bucket cannot be copied, or if
database file references are missing from the selected backend. It includes all
local business data (including skills and post-processor files) and excludes
application settings. See [backup and restore](backup-restore.md) for scope and
legacy MinIO deployment instructions. The
archive contains a versioned manifest, schema revision, application version,
file sizes, and SHA-256 checksums. Store/encrypt the resulting archive with your
organization's approved backup tooling.

Restore is deliberately two-step:

1. `bash scripts/unpack_data.sh snapshot.tar.gz` validates paths, types, manifest,
   and checksums in a temporary staging directory, restores into a disposable
   database, and checks business file references without replacing live data. Add
   `--validate-only` for a successful exit after validation.
2. Re-run with `--confirm-replace`, then type the database name when prompted.

The restore replaces the database from the validated dump and restores local files;
under the default `sql` object-store backend the object bytes come back with the
dump, and only the `s3` backend additionally mirrors bucket contents with removal.
Run `mkb reconcile`, and the readiness probe afterward. Practice this against a
disposable Compose project before depending on a backup. `make restore-drill`
performs checksum/path validation; restores a disposable database and local root
(plus a disposable MinIO instance when the backend is `s3`); then runs full
object-checksummed inventory comparison, reconciliation, and representative
content verification. It is suitable for a scheduled job or integration CI runner
with Docker services and never enables the live replacement path.

Before an application upgrade, create and verify a snapshot, stop job starts,
upgrade the code, then verify readiness and reconciliation.
