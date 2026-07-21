# Backup and restore

Create a snapshot only after checking service health and allowing important jobs to
finish:

```bash
make doctor
make pack out=mkb-snapshot.tar.gz
```

The archive contains a versioned manifest, database dump, required bucket contents,
local data, file sizes, checksums, schema revision, and application version. Store it
encrypted using organization-approved storage and retention controls. A successful
archive creation is not proof of restorability; schedule `make restore-drill` against
disposable infrastructure.

The full drill restores PostgreSQL into a temporary database, starts a disposable
MinIO container, restores local files beneath a temporary root, and then runs inventory
comparison, reconciliation, and content verification against those copies:

```bash
make restore-drill file=mkb-snapshot.tar.gz
```

Set `MKB_RESTORE_DRILL_OUT=/durable/evidence/directory` to retain its JSON reports. For
a historical archive, `MKB_RESTORE_DRILL_BASELINE=/path/to/inventory.json` selects the
matching historical inventory; object identities are enriched with SHA-256 values from
the validated archive manifest. The drill never enables live replacement.

Restore is intentionally two-stage. First validate without mutation:

```bash
make unpack file=mkb-snapshot.tar.gz
```

Then follow the script's explicit `--confirm-replace` instructions. Replacement can
delete current database and bucket state, so stop application processes, verify the
target database/Compose project, and retain a current snapshot first. After restore:

```bash
make migrate
make doctor
.venv/bin/python -m mkb.cli reconcile
```

Investigate checksum, revision, missing-object, or orphan reports before reopening the
service. Never edit a snapshot manifest to bypass validation.
