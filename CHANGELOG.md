# Changelog

MKB follows [Semantic Versioning](https://semver.org/). Until 1.0, minor releases
may refine the SDK while documented public imports and persisted-data compatibility
remain protected by tests and migration gates.

## 0.1.0 - Unreleased

- **Replace MinIO with SQL-backed object storage by default.** Object bytes now live
  in an `object_blobs` table in the database MKB already owns, so a deployment is
  PostgreSQL plus the MKB process — no second container, credential pair, open port,
  or backup artifact. The `minio` and `minio-init` Compose services and the
  `minio_data` volume are gone. Select a backend with `MKB_OBJECT_STORE_BACKEND`
  (`sql` | `file` | `s3`); the `s3` path is unchanged and still supported.
  **Breaking, and there is no migration:** existing `assets` / `processed_assets`
  rows point at keys the SQL store does not have, and reads of them will fail. Delete
  those projects through `delete_project` before first use so metadata and objects go
  away together. Objects left in a MinIO volume are not copied over.
- `pack_data.sh`, `unpack_data.sh` and `restore_drill.sh` are backend-aware: under
  `sql` the object bytes travel inside the PostgreSQL dump and the bucket
  mirror/restore steps are skipped. `unpack_data.sh` renamed `--minio-only` /
  `--no-minio` to `--buckets-only` / `--no-buckets`.
- Introduce the configured `KnowledgeBase` SDK and typed grouped services.
- Add portable SQLite/filesystem repositories, pipelines, jobs, graph operations,
  evidence, schemas, projections, and public extension registries.
- Preserve the existing PostgreSQL materials application through injected
  compatibility adapters.
- Add inventory, reconciliation, preflight comparison, snapshot validation, and
  checksum-gated missing-object repair tooling.
- Split PostgreSQL, S3, PDF, server, materials, Neo4j, and full application support
  into optional installation extras.
