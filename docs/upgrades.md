# Upgrade and migration guide

Before upgrading, read release notes, finish or cancel active jobs, run `make doctor`,
and create a verified snapshot. Stop the API and frontend while leaving PostgreSQL and
MinIO available for migration.

```bash
git pull --ff-only
make bootstrap
make migrate
make check
make doctor
make dev
```

`make bootstrap` preserves an existing `.env`. Compare it manually with
`.env.example` for newly introduced settings. Alembic migrations are forward-only in
normal operation: do not downgrade a production database unless the specific release
documents and tests that path. Rollback normally means restoring the pre-upgrade
snapshot with the matching application version.

After startup, verify readiness, inspect logs for schema/configuration warnings, run
reconciliation, and exercise one read plus one disposable processing workflow. Keep
the pre-upgrade snapshot until operational acceptance is complete.

