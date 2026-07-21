# Changelog

MKB follows [Semantic Versioning](https://semver.org/). Until 1.0, minor releases
may refine the SDK while documented public imports and persisted-data compatibility
remain protected by tests and migration gates.

## 0.1.0 - Unreleased

- Introduce the configured `KnowledgeBase` SDK and typed grouped services.
- Add portable SQLite/filesystem repositories, pipelines, jobs, graph operations,
  evidence, schemas, projections, and public extension registries.
- Preserve the existing PostgreSQL/MinIO materials application through injected
  compatibility adapters.
- Add inventory, reconciliation, preflight comparison, snapshot validation, and
  checksum-gated missing-object repair tooling.
- Split PostgreSQL, S3, PDF, server, materials, Neo4j, and full application support
  into optional installation extras.
