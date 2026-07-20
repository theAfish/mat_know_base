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

