# Upgrade and migration guide

Before upgrading, read release notes, finish or cancel active jobs, run `make doctor`,
and create a verified snapshot. Stop the API and frontend while leaving PostgreSQL and
MinIO available for migration.

```bash
git pull --ff-only
make bootstrap
make check
make doctor
make dev
```

`make bootstrap` preserves an existing `.env`. Compare it manually with
`.env.example` for newly introduced settings. The legacy schema migration chain is
retired; use a verified current database snapshot and roll back by restoring the
pre-upgrade snapshot with its matching application version.

After startup, verify readiness, inspect logs for schema/configuration warnings, run
reconciliation, and exercise one read plus one disposable processing workflow. Keep
the pre-upgrade snapshot until operational acceptance is complete.

For an SDK/data migration, capture inventories before and after the operation, then
run the read-only preservation gate:

```bash
mkb inventory --out migration-snapshots/before.json
# Run only the separately reviewed additive migration or disposable restore here.
mkb inventory --out migration-snapshots/after.json
mkb migration-preflight \
  migration-snapshots/before.json \
  migration-snapshots/after.json \
  --out migration-snapshots/preflight.json
```

The command exits non-zero if an original database ID, object, or local file is
missing, or if object identity/size or a local SHA-256 checksum changed. Additive rows,
objects, and files are reported but do not fail the gate. This comparison never writes
to either backend.

If reconciliation finds a missing processed object but an exact local mirror exists,
first run the checksum-gated dry run:

```bash
mkb restore-missing-artifact ARTIFACT_ID /path/to/local/mirror
```

Applying requires the exact confirmation text and a new, non-overwritten ledger file:

```bash
mkb restore-missing-artifact ARTIFACT_ID /path/to/local/mirror \
  --apply --confirm "RESTORE MISSING OBJECT" \
  --ledger migration-snapshots/repair-ARTIFACT_ID.json
```

The command refuses a size/hash mismatch and never overwrites an existing mismatched
object. Run reconciliation and capture a new inventory after any applied repair.
