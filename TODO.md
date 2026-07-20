# MKB reusable Python package refactor

## Goal

Turn `mat-know-base` into a pip-installable knowledge-processing engine that other
projects can configure with their own database, object store, graph store, schemas,
data types, and pipelines. Keep the current materials-science application working as
the first built-in application of that engine.

The refactor must preserve all currently extracted local data. Existing PostgreSQL
rows, MinIO objects, local processed files, identifiers, evidence links, workflow
versions, projections, feedback, skills, and job history must not be dropped merely
to simplify the new architecture.

## Non-negotiable data-safety rules

- [ ] Never use `reset_db()`, `reset_schema()`, `drop_all()`, `docker compose down -v`,
      destructive restore, or a migration that drops populated tables during this
      refactor.
- [ ] Never rewrite existing UUIDs or S3 bucket/key values unless a reviewed migration
      includes a verified old-to-new mapping and rollback procedure.
- [ ] Treat PostgreSQL, all four MinIO buckets (`raw`, `processed`, `archive`, `temp`),
      and local `data/` content as one dataset. Backing up only the database is not
      sufficient.
- [ ] Make every schema migration additive first: create new tables/columns, backfill,
      verify, switch readers, and only consider cleanup in a later release.
- [ ] Keep legacy tables and adapters readable for at least one complete release after
      the new API becomes the default. For this local-only migration, retaining them
      indefinitely is acceptable.
- [ ] Run data migrations separately from application startup. Importing `mkb` or
      creating a client must never silently migrate or delete data.
- [ ] Any migration that changes persisted data must support a dry run, report counts,
      be restartable/idempotent, and record its completion in a migration ledger.
- [ ] Do not declare a phase complete until the pre-refactor snapshot passes a restore
      drill and the post-migration reconciliation report passes.

## Phase 0 — Freeze and inventory the local dataset

- [ ] Stop starting new extraction, projection, graph, review, and maintenance jobs;
      allow active jobs to reach a terminal state.
- [ ] Record the current git commit, package version, Alembic revision, configuration,
      PostgreSQL version, MinIO version, and Docker Compose project name in a migration
      manifest. Do not put credentials into the manifest.
- [ ] Run the existing operational checks:

      ```bash
      make up
      make doctor
      .venv/bin/python -m mkb.cli reconcile
      ```

- [x] Add an inventory command that emits JSON containing row counts and stable IDs for
      every persistent model, including projects, groups, assets, project-asset links,
      processed assets, frames, extraction passes, spaces, projections, feedback,
      graph reviews, raw/canonical workflows, schema proposals/revisions, workflow
      maintenance/index entries, custom skills, post-processor scripts, and jobs.
- [x] Extend inventory with per-bucket object counts, total bytes, and checksums or a
      deterministic object-key manifest.
- [x] Inventory local files under at least `data/papers`, `data/processed`,
      `data/uploads`, `data/inbox`, and `data/runtime_settings.json` when present.
- [ ] Detect broken references before migration: missing S3 objects, orphan objects,
      missing local mirrors, dangling foreign keys, duplicate logical identifiers, and
      records whose stored schema/version cannot be resolved.
- [ ] Save the inventory outside ephemeral Docker volumes, for example under a
      timestamped `migration-snapshots/` directory that is excluded from git.

### Required backup gate

- [x] Create a named full snapshot using the existing packer:

      ```bash
      make pack out=migration-snapshots/pre-sdk-refactor.tar.gz
      ```

- [x] Run the validation-only restore drill and retain its successful output with the
      manifest. The named 8.3 GB snapshot passed checksum validation and restored into
      a disposable PostgreSQL database on 2026-07-20; live replacement was not enabled.
- [ ] Additionally restore the named snapshot into disposable infrastructure and run
      inventory/reconciliation there. The existing validation-only drill checks the
      archive and PostgreSQL restore; the expanded drill must also prove MinIO and local
      file restoration without touching live data.
- [ ] Keep the pre-refactor snapshot until all old data has been read successfully
      through the new API and a second post-migration snapshot has passed the same drill.

## Phase 1 — Define and test the supported SDK contract

- [x] Replace the global-first design with an explicit configured application object:

      ```python
      from mkb import KnowledgeBase

      kb = KnowledgeBase.from_url(
          database_url="postgresql+psycopg://...",
          object_store_url="s3://raw?endpoint=http://localhost:9000",
      )
      ```

- [x] Allow at least two independently configured `KnowledgeBase` instances in one
      Python process without shared settings, engines, sessions, job managers, or
      registries.
- [x] Keep `from mkb import api` as a compatibility surface alongside an explicitly
      configured default client. Mark it deprecated only after feature parity exists.
- [x] Define the public import boundary. Consumers must not need `mkb.db`, `mkb.web`,
      ORM models, storage internals, or service-private functions.
- [x] Remove private names and database session factories from the future public
      `__all__`; temporary import-compatible port aliases remain available but are not
      included in wildcard imports.
- [x] Introduce typed public models (Pydantic models or dataclasses) for collections,
      sources, artifacts, records, schemas, entities, relations, evidence, pipeline
      runs, jobs, pages, and operation receipts.
- [x] Permit `model_dump(mode="json")` or an equivalent stable serialization method on
      public models.
- [x] Standardize exceptions: `MKBError`, `NotFoundError`, `ConflictError`,
      `ValidationError`, `BackendUnavailableError`, `ProviderError`, and
      `PipelineExecutionError`.
- [x] Standardize behavior for the supported grouped SDK: return a typed value on
      success, use `None` only for optional lookups, and raise typed exceptions. Legacy
      compatibility methods retain their dictionary contracts until Phase 7 migration.
- [x] Add API contract tests for every currently supported grouped SDK method, including
      a deliberate method inventory plus success, optional lookup, errors, idempotency,
      serialization, repository, graph, registry, transaction, and pipeline coverage.
      New grouped services must extend the inventory as they replace the legacy facade.

## Phase 2 — Remove global configuration and persistence coupling

- [x] Introduce an immutable `MKBConfig` that can be created from explicit Python
      values. Environment and YAML loading should be optional constructors, not import-
      time behavior.
- [x] Move engine and session creation out of module globals in `mkb.db.engine` and into
      an injected SQLAlchemy adapter owned by `KnowledgeBase`. Legacy facade aliases
      are lazy compatibility proxies and no longer construct engines at import time.
- [x] Inject object storage, graph storage, model provider, parser registry, pipeline
      registry, and job backend into the application object. Provider and backend
      behavior is implemented in their later feature phases; Phase 2 owns lifecycle,
      isolation, and capability composition.
- [x] Define explicit lifecycle methods or context-manager support so connections and
      worker resources are released predictably.
- [x] Add explicit transaction scopes. Collection, source, artifact, record, schema,
      and projection metadata share one commit/rollback boundary; object-backed writes
      use reverse-order best-effort compensation on rollback:

      ```python
      with kb.transaction() as tx:
          collection = tx.collections.create(name="Experiment 42")
          tx.sources.add_text(collection.id, notes)
      ```

- [x] Document that PostgreSQL, object storage, and external graph databases cannot
      share one ACID transaction. Use stable IDs, staging states, idempotent writes,
      an outbox/event pattern, and compensating cleanup for cross-store operations.
- [x] Prove with tests that the current local PostgreSQL and MinIO configuration works
      through the injected adapters before changing any schema.

## Phase 3 — Introduce generic domain concepts without discarding old records

- [ ] Define infrastructure-independent concepts:
  - [x] `Collection`: a typed logical grouping of data, initially mapped read-only to
        existing `research_projects` rows through the injected SQLAlchemy adapter.
  - [x] `Source`: a typed ingested input, initially mapped read-only to existing assets
        with collection membership and content access through the object-store port.
  - [x] `Artifact`: a typed derived output, initially mapped read-only to existing
        processed assets with content access through the object-store port.
  - [x] `Record`: typed structured data mapped read-only to current knowledge frames.
  - [x] `Schema`: typed extraction policy mapped read-only to current spaces.
  - [x] `Entity` and `Relation`: typed, serializable graph elements with an in-memory
        adapter and grouped graph service.
  - [x] `Evidence`: typed, serializable provenance linking outputs to sources/artifacts;
        persistence adapters remain pending.
  - [x] `PipelineRun` and `StepRun`: typed local execution and provenance records.
- [ ] Keep materials concepts as a supported extension and map them explicitly:
  - research project -> collection
  - asset -> source
  - processed asset -> artifact
  - [x] knowledge frame -> record
  - [x] space -> schema/extraction profile
  - [x] projection -> schema-specific record
  - raw workflow -> specialized workflow record
- [x] Prefer compatibility views/adapters over immediately renaming old tables. The
      first implementation may read existing `research_projects`, `assets`,
      `processed_assets`, `knowledge_frames`, `spaces`, and `projections` directly and
      present generic typed models.
- [x] Preserve the original IDs in generic models. If a new universal ID is needed, add
      it alongside the legacy ID and maintain a unique mapping table.
- [ ] Preserve raw JSON payloads, schema versions, timestamps, status fields, source
      paths, S3 locations, evidence, review annotations, and agent notes losslessly.
      The current collection/source/artifact/record/schema/projection adapters preserve
      their mapped fields; dedicated evidence models and verification are still pending.
- [ ] Add round-trip tests using a sanitized copy of representative current records:
      legacy row -> new typed model -> serialized form -> model, with no meaningful
      field loss.

## Phase 4 — Define ports and default adapters

- [ ] Add narrow protocols for collection/source/artifact/record repositories, object
      storage, graph storage, vector search, parsers, model providers, and jobs.
- [ ] Do not create one artificial storage interface for relational, object, vector,
      and graph data. Keep the ports distinct and compose them in `KnowledgeBase`.
- [ ] Declare adapter capabilities such as transactions, vector search, full-text
      search, streaming, graph traversal, and bulk upsert. Fail early when a pipeline
      requires an unsupported capability.
- [ ] Implement and test these initial adapters:
  - Existing PostgreSQL/pgvector schema adapter, including all current local data.
  - Existing MinIO/S3 adapter, preserving current buckets and keys.
  - Filesystem object store for lightweight local projects and tests.
  - [x] SQLite metadata repository for a minimal pip-package quickstart, including
        portable collections, sources, artifacts, records, schemas, projections, and an
        additive schema-version ledger.
  - [x] In-memory or NetworkX graph adapter for a minimal local graph setup.
- [ ] Add Neo4j or another external graph adapter later as an optional extra; it is not
      required to migrate the current local dataset.
- [ ] Add repository conformance tests that every adapter must pass, plus capability-
      specific tests.

## Phase 5 — Make custom pipelines a first-class public API

- [x] Implement `Pipeline`, `Step`, `StepContext`, `PipelineRun`, and `StepRun`.
- [x] Let steps declare typed inputs/outputs, configuration schema, required adapter
      capabilities, deterministic/cache behavior, retry policy, timeout, side effects,
      and progress events.
- [x] Support sequential pipelines first, then DAG dependencies when the contract is
      stable.
- [x] Support synchronous local execution:

      ```python
      run = kb.pipelines.run(
          pipeline,
          inputs={"source_id": source.id},
          parameters={"model": "openai/qwen-plus"},
      )
      ```

- [ ] Support durable submission using the same pipeline definition:

      ```python
      job = kb.pipelines.submit(pipeline, inputs={"source_id": source.id})
      completed = kb.jobs.wait(job.id)
      ```

- [ ] Add checkpointing, cancellation, resumption, structured progress, per-step logs,
      provenance, stable run IDs, and idempotency keys.
- [ ] Implement caching only after deterministic cache keys include step version,
      configuration, source fingerprint, model identity, and relevant schema version.
- [ ] Convert current operations into built-in steps and pipelines without changing
      output semantics: ingest, process, frame extraction, projection, graph extraction,
      workflow extraction, schema review, and feedback review.
- [x] Ensure old extracted records can be used as pipeline inputs without reprocessing
      their source documents.
- [x] Allow consumer registration of parsers, steps, schemas, and pipelines without
      editing the MKB package. Parser, standalone-step, and pipeline registries are
      isolated per client; schema registration is persisted by the configured schema
      repository.

## Phase 6 — Expand the Python API to full application parity

- [ ] Provide grouped services on `KnowledgeBase`:
  - `kb.collections`: create/get/list/update/delete and grouping.
  - `kb.sources`: add file/bytes/text/URI/records, list, inspect, and stream content.
  - `kb.artifacts`: list, register, inspect, and stream content.
  - `kb.records`: create/get/list/query/export with evidence.
  - `kb.schemas`: create/version/get/list/update/delete.
  - `kb.graph`: entity/relation upsert, query, traversal, extraction, and review.
  - `kb.pipelines`: register/get/list/run/submit/resume.
  - `kb.jobs`: submit/get/list/wait/cancel and event streaming.
  - `kb.feedback`: create/list/review/resolve.
  - `kb.skills`: create/get/list/delete.
  - `kb.post_processors`: register/get/list/delete.
  - `kb.settings`: inspect effective configuration without exposing secrets.
  - `kb.maintenance`: inventory, reconcile, backup metadata, and safe cleanup plans.
- [ ] Add missing simple lookups such as `get_project`/`get_collection`; never implement
      a singular lookup by scanning a limited list result.
- [ ] Add public source/artifact content access instead of requiring ORM and S3 imports.
- [ ] Support all useful ingestion forms:
  - managed file copy
  - bytes and text
  - directory convenience ingestion
  - external URI/reference without copying
  - structured record batches
  - externally processed artifact registration
- [ ] Provide both sync and async clients only where async behavior is real. Do not make
      synchronous ORM/storage calls appear asynchronous through superficial wrappers.
- [ ] Generate API reference documentation from the typed public surface and include
      complete local, PostgreSQL/MinIO, custom pipeline, and migration examples.

## Phase 7 — Make the CLI, FastAPI server, and materials app consume the SDK

- [ ] Enforce this dependency direction:

      ```text
      React -> FastAPI -> KnowledgeBase/application services -> core -> ports
      CLI -------------> KnowledgeBase/application services -> core -> ports
      Python user -----> KnowledgeBase/application services -> core -> ports
      ```

- [ ] Move direct ORM/S3 access out of web routes, including raw and processed asset
      preview/download paths.
- [ ] Move web-only job management behind `kb.jobs` so notebooks and other applications
      can use the same durable job behavior.
- [ ] Move settings, skills, assistant sessions, post-processor scripts, diagnostics,
      and maintenance behind supported application services where appropriate.
- [ ] Rewrite CLI commands to call the same public SDK. Keep interactive confirmation
      in the CLI while destructive SDK methods require explicit confirmation tokens or
      policies.
- [ ] Keep current React behavior as an integration test for feature parity.
- [ ] Keep current materials APIs as `kb.materials.frames`, `kb.materials.spaces`,
      `kb.materials.projections`, and `kb.materials.workflows`, or provide an equivalent
      `MaterialsKnowledgeBase` extension.
- [ ] Do not remove the legacy facade until the CLI, HTTP API, UI, examples, and local
      data validation all pass through the new implementation.

## Phase 8 — Migrate the current local data safely

### Migration strategy

- [ ] Prefer an in-place, additive migration so the existing Compose PostgreSQL and
      MinIO services remain the initial production adapters.
- [ ] Add new generic tables only when compatibility views/adapters are insufficient.
      Suggested additions include pipeline definitions/runs/step runs, generic record
      metadata, evidence links, backend registrations, and legacy-ID mappings.
- [ ] Add Alembic upgrades only. During the preservation window, downgrades for new
      migrations must not drop old tables or old columns containing user data; a safe
      downgrade may instead remove only demonstrably empty new structures or refuse.
- [ ] Backfill in bounded batches with stable ordering and commits. Store the last
      completed key/checkpoint so interruption and retry cannot duplicate records.
- [ ] Make backfills use upsert plus deterministic keys. Running the migration twice
      must produce identical counts and mappings.
- [ ] Initially leave S3 objects in their current buckets and keys. Store references to
      those locations in new models instead of copying blobs unnecessarily.
- [ ] Initially leave local processed mirrors in place. Add a storage reference rather
      than moving files during schema migration.
- [ ] Add dual-read support: prefer the new representation when present and fall back to
      the legacy representation. Add dual-write only for the shortest necessary
      transition and test it carefully.
- [ ] Compare pre- and post-migration inventories. Every old persistent ID must be
      accounted for as migrated, intentionally retained behind an adapter, or explicitly
      classified as ephemeral.
- [ ] Verify content, not only counts: sample and checksum raw assets, processed
      artifacts, frames, projection payloads, workflow graphs, and evidence references.
- [ ] Run old-versus-new query comparisons for representative projects, frames, spaces,
      projections, graphs, workflows, feedback, skills, and exports.
- [ ] Run the full Python tests, frontend build, API integration tests, and a local UI
      smoke test against the migrated data.
- [ ] Create and restore-drill a post-migration snapshot before changing default readers.

### Cutover and rollback

- [ ] Cut over one read path at a time behind a configuration flag. Start with read-only
      list/get/export operations, then writes, then long-running pipelines.
- [ ] Keep the legacy read flag available until all local data has been exercised through
      the new SDK.
- [ ] Rollback means switching readers/writers back to legacy adapters and restoring the
      pre-refactor snapshot only if additive changes somehow corrupted existing state.
      A normal code rollback should not require restoring data.
- [ ] Never run the live replacement path in `unpack_data.sh` unless the current live
      dataset has first been snapshotted and the exact target has been confirmed.
- [ ] After cutover, run:

      ```bash
      make doctor
      .venv/bin/python -m mkb.cli reconcile
      make check
      ```

- [ ] Retain the pre- and post-migration snapshots until at least one complete local work
      cycle has succeeded: ingest, process, extract, project, graph/workflow operations,
      review, query, and export.

## Phase 9 — Packaging and distribution

- [ ] Keep the base wheel lightweight and provide optional extras, for example:
  - `mat-know-base[postgres]`
  - `mat-know-base[s3]`
  - `mat-know-base[pdf]`
  - `mat-know-base[neo4j]`
  - `mat-know-base[server]`
  - `mat-know-base[materials]`
  - `mat-know-base[all]`
- [ ] Ensure `pip install mat-know-base` supports a minimal SQLite + filesystem example
      without Docker, PostgreSQL, MinIO, FastAPI, React, or MinerU.
- [ ] Keep Alembic resources and built-in pipeline/schema assets inside the wheel and
      resolve them with `importlib.resources`, not the current working directory.
- [ ] Remove assumptions that `config.yaml`, `.env`, `alembic.ini`, `data/`, or the repo
      root exists beside the installed package.
- [ ] Add versioned database compatibility metadata and refuse to open a database newer
      than the installed library understands.
- [ ] Adopt semantic versioning, a deprecation policy, a public API compatibility test,
      changelog, and migration guide.
- [ ] Test wheel and source distribution installation in clean environments for the
      minimum and supported Python versions.
- [ ] Publish release candidates locally first and install the built wheel into a
      separate external example project before publishing publicly.

## External example repository acceptance test

Before declaring the reusable SDK ready, a project depending only on the built wheel
must be able to:

- [ ] Create a new SQLite/filesystem knowledge base.
- [ ] Connect to the existing local PostgreSQL/MinIO knowledge base and read all current
      extracted data without changing it.
- [ ] Create a separate database with no state leaking between the two clients.
- [ ] Register a custom source type, parser, schema, and at least two custom pipeline
      steps.
- [ ] Ingest arbitrary file, text, and structured-record data.
- [ ] Run a custom pipeline and persist structured records, evidence, and graph relations.
- [ ] Query, inspect, and export results using only public imports.
- [ ] Resume or retry an interrupted pipeline without duplicating outputs.
- [ ] Run without importing `mkb.db`, `mkb.web`, ORM models, service-private modules, or
      repository source files.

## Definition of done

- [ ] The current materials application, CLI, HTTP API, and React UI work through the new
      application services.
- [ ] Current local data is fully readable and usable; no required re-extraction is
      necessary.
- [ ] Pre- and post-migration snapshots both pass restore drills.
- [ ] Inventory counts, identifier mappings, object references, and representative
      content checks pass.
- [ ] Two independently configured knowledge bases work in one process.
- [ ] An external project can define and run a custom pipeline using only the installed
      public package.
- [ ] The old facade has either full compatibility coverage or a documented, tested
      deprecation path.
- [ ] No destructive cleanup of legacy data is required for the first stable SDK release.
