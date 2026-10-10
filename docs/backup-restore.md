# Backup and restore

`make pack` creates a **business data** snapshot. It includes the entire PostgreSQL
database (spaces, schemas, prompts, projections, skill registrations, feedback,
jobs, graphs, and other tables), object storage, and local business files.

The local payload includes all of `data/`, including `skills/` (with supporting
scripts and reference files), `post_processor_scripts/`, `exports/`, uploads,
papers, and processed results. Empty directories are preserved. Configured
processed/file-object directories elsewhere **inside the project** are included as
well; external paths and symlinks fail explicitly rather than being silently lost.

Application settings are deliberately excluded: `.env`, `config.yaml`,
`data/runtime_settings.json`, and the configured runtime-settings file. Source
code, dependencies, and logs are also outside this business snapshot. File
permissions follow the normal process umask; the scripts do not impose an
owner-only archive mode. Business content itself is not redacted.

Use the matching project version and its Python environment (`.venv/bin/python`
by default, or `PYTHON=/path/to/python`). S3 snapshots require the project's
optional `boto3` dependency. PostgreSQL dump/restore uses the project's Docker
Compose `postgres` service. S3 copying uses the configured endpoint directly and
no longer requires an `mc` image or a Compose service named `minio`.

## Create

Stop application writes and allow background jobs to finish before starting; the
database and external files are copied in separate steps. Keep writes stopped
until the snapshot completes.

```bash
make pack out=backup-snapshots/mkb-business.tar.gz
```

Configuration uses the application's environment → `.env` → `config.yaml` →
default precedence. **Select the backend holding the actual objects.** In older
installations, files may still be in MinIO even though newer code defaults to
`sql`. For those installations:

```bash
MKB_OBJECT_STORE_BACKEND=s3 make pack out=backup-snapshots/mkb-business.tar.gz
```

The script checks database file references before exporting. An empty SQL object
table with existing asset references fails instead of producing an incomplete
snapshot. For `sql`, object bytes are in the database dump; for `s3`, configured
buckets are mirrored under `minio/`; for `file`, the local object root is included.
The snapshot records its backend without recording connection credentials.

Save archives outside the business directories. Existing archive paths are never
overwritten; a failed operation removes its staging files and partial output.
The version-2 manifest covers all files, including nested `manifest.json` files,
with sizes and SHA-256 checksums. Version-1 archives remain readable, although
files omitted by an old packer cannot be reconstructed by the new unpacker.

## Validate and restore

Validation extracts into temporary storage, verifies the file inventory and all
checksums, restores PostgreSQL into a disposable database, and checks its object,
skill, and post-processor file references against the snapshot. It does not
replace live data:

```bash
bash scripts/unpack_data.sh backup-snapshots/mkb-business.tar.gz --validate-only
```

The older `make unpack file=...` entrypoint also validates, then exits with status
2 to request explicit replacement. `--validate-only` exits successfully after
validation. On a target installation, configure the matching object backend and
connection settings yourself; business snapshots do not transfer settings.
For file storage, use the same project-relative object root. For S3, configure the
target endpoint and credentials before restoring.

```bash
MKB_OBJECT_STORE_BACKEND=s3 bash scripts/unpack_data.sh \
  backup-snapshots/mkb-business.tar.gz --confirm-replace
```

Full replacement requires typing the target database name. PostgreSQL restoration
stops on errors and runs in one transaction; S3 and local-file operations are
separate. S3 restoration removes target objects absent from the snapshot, while
local-file restoration overlays archived files and preserves other local files
and settings. Retain a current target backup and stop application processes first.
Backend mismatches block full replacement instead of silently skipping buckets.
Partial restore flags remain available: `--pg-only`, `--buckets-only`,
`--local-only`, `--no-pg`, `--no-buckets`, and `--no-local`.

## Full disposable drill

For a stronger test, restore into a disposable database, local directory, and
(for S3) MinIO instance; then compare inventories and run SDK reconciliation and
content verification:

```bash
MKB_OBJECT_STORE_BACKEND=s3 \
MKB_RESTORE_DRILL_OUT=backup-snapshots/restore-evidence \
make restore-drill file=backup-snapshots/mkb-business.tar.gz
```

`MKB_RESTORE_DRILL_MINIO_IMAGE` can select an already installed MinIO image. The
drill never enables live replacement. Without a supplied historical baseline it
compares against current live data, so run it before live data changes.
`MKB_RESTORE_DRILL_BASELINE=/path/to/inventory.json` supplies a saved baseline;
use a baseline from the same inventory version and local-path layout. New
inventories include all `data/` business files and exclude runtime settings.

After a real restore, run `make doctor`, `mkb reconcile`, and application readiness
checks before reopening the service. Investigate missing-object and checksum
failures; do not edit a manifest to bypass validation.
