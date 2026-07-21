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

- [x] Never use `reset_db()`, `reset_schema()`, `drop_all()`, `docker compose down -v`,
      destructive restore, or a migration that drops populated tables during this
      refactor.
- [x] Never rewrite existing UUIDs or S3 bucket/key values unless a reviewed migration
      includes a verified old-to-new mapping and rollback procedure.
- [x] Treat PostgreSQL, all four MinIO buckets (`raw`, `processed`, `archive`, `temp`),
      and local `data/` content as one dataset. Backing up only the database is not
      sufficient.
- [x] Make every schema migration additive first: create new tables/columns, backfill,
      verify, switch readers, and only consider cleanup in a later release.
- [x] Keep legacy tables and adapters readable for at least one complete release after
      the new API becomes the default. For this local-only migration, retaining them
      indefinitely is acceptable.
- [x] Run data migrations separately from application startup. Importing `mkb` or
      creating a client must never silently migrate or delete data.
- [x] Any migration that changes persisted data must support a dry run, report counts,
      be restartable/idempotent, and record its completion in a migration ledger.
- [x] Do not declare a phase complete until the pre-refactor snapshot passes a restore
      drill and the post-migration reconciliation report passes.
      Both full disposable drills and the post-repair live reconciliation passed on
      2026-07-21 with retained JSON evidence.

## Phase 0 — Freeze and inventory the local dataset

- [x] Stop starting new extraction, projection, graph, review, and maintenance jobs;
      allow active jobs to reach a terminal state.
      The 2026-07-21 freeze check found all five persisted jobs `COMPLETED`.
- [x] Record the current git commit, package version, Alembic revision, configuration,
      PostgreSQL version, MinIO version, and Docker Compose project name in a migration
      manifest. Do not put credentials into the manifest.
- [x] Run the existing operational checks:

      ```bash
      make up
      make doctor
      .venv/bin/python -m mkb.cli reconcile
      ```

      `make doctor`, service health, and reconciliation pass. The one missing
      40,160-byte processed object was restored from its exact checksummed local mirror
      under an explicit confirmation token and recorded migration ledger.

- [x] Add an inventory command that emits JSON containing row counts and stable IDs for
      every persistent model, including projects, groups, assets, project-asset links,
      processed assets, frames, extraction passes, spaces, projections, feedback,
      graph reviews, raw/canonical workflows, schema proposals/revisions, workflow
      maintenance/index entries, custom skills, post-processor scripts, and jobs.
- [x] Extend inventory with per-bucket object counts, total bytes, and checksums or a
      deterministic object-key manifest.
- [x] Inventory local files under at least `data/papers`, `data/processed`,
      `data/uploads`, `data/inbox`, and `data/runtime_settings.json` when present.
- [x] Detect broken references before migration: missing S3 objects, orphan objects,
      missing local mirrors, dangling foreign keys, duplicate logical identifiers, and
      records whose stored schema/version cannot be resolved.
      The 2026-07-21 scan found and repaired one missing processed object. The 379
      unchanged legacy processed objects whose asset rows no longer exist are retained,
      not deleted. Final reconciliation reports zero missing references and content
      verification reports zero sampled checksum mismatches.
- [x] Save the inventory outside ephemeral Docker volumes, for example under a
      timestamped `migration-snapshots/` directory that is excluded from git.
      The full 2026-07-21 inventory contains 22 tables, 21,500 objects, and 21,931
      checksummed local files and is retained under the git-ignored snapshot directory.

### Required backup gate

- [x] Create a named full snapshot using the existing packer:

      ```bash
      make pack out=migration-snapshots/pre-sdk-refactor.tar.gz
      ```

- [x] Run the validation-only restore drill and retain its successful output with the
      manifest. The named 8.3 GB snapshot passed checksum validation and restored into
      a disposable PostgreSQL database on 2026-07-20; live replacement was not enabled.
- [x] Additionally restore the named snapshot into disposable infrastructure and run
      inventory/reconciliation there. The existing validation-only drill checks the
      archive and PostgreSQL restore; the expanded drill must also prove MinIO and local
      file restoration without touching live data.
      The original snapshot remains untouched; a self-contained repaired copy adds only
      the exact runtime-settings file and missing object proven by the pre-refactor
      inventory. Its full PostgreSQL/MinIO/local restore, inventory, reconciliation, and
      content verification passed in disposable infrastructure.
- [x] Keep the pre-refactor snapshot until all old data has been read successfully
      through the new API and a second post-migration snapshot has passed the same drill.
      The original and repaired pre-refactor archives and the drilled post-refactor v2
      archive are all retained under `migration-snapshots/`.

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

- [x] Define infrastructure-independent concepts:
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
  - [x] `Evidence`: typed, serializable provenance linking outputs to sources/artifacts,
        with portable additive persistence and transaction support.
  - [x] `PipelineRun` and `StepRun`: typed local execution and provenance records.
- [x] Keep materials concepts as a supported extension and map them explicitly:
  - [x] research project -> collection
  - [x] asset -> source
  - [x] processed asset -> artifact
  - [x] knowledge frame -> record
  - [x] space -> schema/extraction profile
  - [x] projection -> schema-specific record
  - [x] raw workflow -> specialized lossless workflow record under `kb.materials`
- [x] Prefer compatibility views/adapters over immediately renaming old tables. The
      first implementation may read existing `research_projects`, `assets`,
      `processed_assets`, `knowledge_frames`, `spaces`, and `projections` directly and
      present generic typed models.
- [x] Preserve the original IDs in generic models. If a new universal ID is needed, add
      it alongside the legacy ID and maintain a unique mapping table.
- [x] Preserve raw JSON payloads, schema versions, timestamps, status fields, source
      paths, S3 locations, evidence, review annotations, and agent notes losslessly.
      SQLite timestamp rehydration restores UTC metadata lost by its datetime storage.
- [x] Add round-trip tests using a sanitized copy of representative current records:
      legacy row -> new typed model -> serialized form -> model, with no meaningful
      field loss.

## Phase 4 — Define ports and default adapters

- [x] Add narrow protocols for collection/source/artifact/record repositories, object
      storage, graph storage, vector search, parsers, model providers, and jobs.
- [x] Do not create one artificial storage interface for relational, object, vector,
      and graph data. Keep the ports distinct and compose them in `KnowledgeBase`.
- [x] Declare adapter capabilities such as transactions, vector search, full-text
      search, streaming, graph traversal, and bulk upsert. Fail early when a pipeline
      requires an unsupported capability.
- [x] Implement and test these initial adapters:
  - [x] Existing PostgreSQL/pgvector schema adapter, including all current local data.
  - [x] Existing MinIO/S3 adapter, preserving current buckets and keys.
  - [x] Filesystem object store for lightweight local projects and tests.
  - [x] SQLite metadata repository for a minimal pip-package quickstart, including
        portable collections, sources, artifacts, records, schemas, projections, and an
        additive schema-version ledger.
  - [x] In-memory or NetworkX graph adapter for a minimal local graph setup.
- [x] Add Neo4j or another external graph adapter later as an optional extra; it is not
      required to migrate the current local dataset.
      `Neo4jGraphStore` is lazily loaded behind the `neo4j` extra, uses fixed labels and
      relationship types, and passes driver-injected graph-store conformance tests.
- [x] Add repository conformance tests that every adapter must pass, plus capability-
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

- [x] Support durable submission using the same pipeline definition:

      ```python
      job = kb.pipelines.submit(pipeline, inputs={"source_id": source.id})
      completed = kb.jobs.wait(job.id)
      ```

- [x] Add checkpointing, cancellation, resumption, structured progress, per-step logs,
      provenance, stable run IDs, and idempotency keys.
- [x] Implement caching only after deterministic cache keys include step version,
      configuration, source fingerprint, model identity, and relevant schema version.
- [x] Convert current operations into built-in steps and pipelines without changing
      output semantics: ingest, process, frame extraction, projection, graph extraction,
      workflow extraction, schema review, and feedback review.
- [x] Ensure old extracted records can be used as pipeline inputs without reprocessing
      their source documents.
- [x] Allow consumer registration of parsers, steps, schemas, and pipelines without
      editing the MKB package. Parser, standalone-step, and pipeline registries are
      isolated per client; schema registration is persisted by the configured schema
      repository.

## Phase 6 — Expand the Python API to full application parity

- [x] Provide grouped services on `KnowledgeBase`:
  - [x] `kb.collections`: create/get/list/update/delete and grouping.
  - [x] `kb.sources`: add file/bytes/text/URI/records, list, inspect, and stream content.
  - [x] `kb.artifacts`: list, register, inspect, and stream content.
  - [x] `kb.records`: create/get/list/query/export with evidence.
  - [x] `kb.schemas`: create/version/get/list/update/delete.
  - [x] `kb.graph`: entity/relation upsert, query, traversal, extraction, and review.
  - [x] `kb.pipelines`: register/get/list/run/submit/resume.
  - [x] `kb.jobs`: submit/get/list/wait/cancel and event streaming.
  - [x] `kb.feedback`: create/list/review/resolve.
  - [x] `kb.skills`: create/get/list/delete.
  - [x] `kb.post_processors`: register/get/list/delete.
  - [x] `kb.settings`: inspect effective configuration without exposing secrets.
  - [x] `kb.maintenance`: inventory, reconcile, backup metadata, and safe cleanup plans.
- [x] Add missing simple lookups such as `get_project`/`get_collection`; never implement
      a singular lookup by scanning a limited list result.
- [x] Add public source/artifact content access instead of requiring ORM and S3 imports.
- [x] Support all useful ingestion forms:
  - [x] managed file copy
  - [x] bytes and text
  - [x] directory convenience ingestion
  - [x] external URI/reference without copying
  - [x] structured record batches
  - [x] externally processed artifact registration
- [x] Provide both sync and async clients only where async behavior is real. The public
      SDK remains explicitly synchronous because its current injected ports are
      synchronous; durable jobs provide non-blocking application execution without fake
      `async` wrappers.
- [x] Generate API reference documentation from the typed public surface and include
      complete local, PostgreSQL/MinIO, custom pipeline, and migration examples.

## Phase 7 — Make the CLI, FastAPI server, and materials app consume the SDK

- [x] Enforce this dependency direction:

      ```text
      React -> FastAPI -> KnowledgeBase/application services -> core -> ports
      CLI -------------> KnowledgeBase/application services -> core -> ports
      Python user -----> KnowledgeBase/application services -> core -> ports
      ```

- [x] Move direct ORM/S3 access out of web routes, including raw and processed asset
      preview/download paths.
- [x] Move web-only job management behind `kb.jobs` so notebooks and other applications
      can use the same durable job behavior.
- [x] Move settings, skills, assistant sessions, post-processor scripts, diagnostics,
      and maintenance behind supported application services where appropriate.
- [x] Rewrite CLI commands to call the same public SDK. Keep interactive confirmation
      in the CLI while destructive SDK methods require explicit confirmation tokens or
      policies.
- [x] Keep current React behavior as an integration test for feature parity.
- [x] Keep current materials APIs as `kb.materials.frames`, `kb.materials.spaces`,
      `kb.materials.projections`, and `kb.materials.workflows`, or provide an equivalent
      `MaterialsKnowledgeBase` extension.
- [x] Do not remove the legacy facade until the CLI, HTTP API, UI, examples, and local
      data validation all pass through the new implementation.
      The compatibility facade remains present; CLI/API/UI/example and restored-data
      validation pass through the application-service implementation.

## Phase 8 — Migrate the current local data safely

### Migration strategy

- [x] Prefer an in-place, additive migration so the existing Compose PostgreSQL and
      MinIO services remain the initial production adapters.
- [x] Add new generic tables only when compatibility views/adapters are insufficient.
      Suggested additions include pipeline definitions/runs/step runs, generic record
      metadata, evidence links, backend registrations, and legacy-ID mappings.
- [x] Add Alembic upgrades only. During the preservation window, downgrades for new
      migrations must not drop old tables or old columns containing user data; a safe
      downgrade may instead remove only demonstrably empty new structures or refuse.
      Migration `0023_durable_jobs` now refuses to drop its table when job history exists
      and permits removal only when the newly added table is empty.
- [x] Backfill in bounded batches with stable ordering and commits. Store the last
      completed key/checkpoint so interruption and retry cannot duplicate records.
      No representation backfill was introduced: compatibility adapters read the
      preserved tables directly, so there is no resumable batch to execute.
- [x] Make backfills use upsert plus deterministic keys. Running the migration twice
      must produce identical counts and mappings.
      No data-copy backfill was required; the one object repair is checksum-gated,
      idempotent, dry-run capable, and ledgered.
- [x] Initially leave S3 objects in their current buckets and keys. Store references to
      those locations in new models instead of copying blobs unnecessarily.
- [x] Initially leave local processed mirrors in place. Add a storage reference rather
      than moving files during schema migration.
- [x] Add dual-read support: prefer the new representation when present and fall back to
      the legacy representation. Add dual-write only for the shortest necessary
      transition and test it carefully.
      Compatibility adapters deliberately use the preserved canonical tables and object
      references, avoiding a second representation and dual-write divergence.
- [x] Compare pre- and post-migration inventories. Every old persistent ID must be
      accounted for as migrated, intentionally retained behind an adapter, or explicitly
      classified as ephemeral.
  - [x] Provide a deterministic, read-only `mkb migration-preflight` comparator that
        blocks on missing database IDs, missing/changed objects, and missing/changed
        local files while allowing additive data.
- [x] Verify content, not only counts: sample and checksum raw assets, processed
      artifacts, frames, projection payloads, workflow graphs, and evidence references.
      The saved live and restored verification reports pass all sampled source/artifact
      bundle checksums. Both restore inventories additionally SHA-256 all 21,501 objects.
- [x] Run old-versus-new query comparisons for representative projects, frames, spaces,
      projections, graphs, workflows, feedback, skills, and exports.
      The saved live report compares 12 complete ID mappings, 45 deterministic payload
      samples, and two public exports with zero blockers.
- [x] Run the full Python tests, frontend build, API integration tests, and a local UI
      smoke test against the migrated data.
      `make check` passed 280 Python tests plus TypeScript lint/build and bundle budgets;
      an isolated current-source API returned healthy readiness and OpenAPI responses.
- [x] Create and restore-drill a post-migration snapshot before changing default readers.
      `post-sdk-refactor-20260721-v2.tar.gz` includes PostgreSQL, all buckets, local data,
      and runtime settings and passed the full disposable drill with zero blockers.

### Cutover and rollback

- [x] Cut over one read path at a time behind a configuration flag. Start with read-only
      list/get/export operations, then writes, then long-running pipelines.
      Routes were moved incrementally to application services. A persisted-reader flag
      was unnecessary because both implementations use the same retained legacy tables.
- [x] Keep the legacy read flag available until all local data has been exercised through
      the new SDK.
      The legacy facade and adapters remain available as the rollback surface.
- [x] Rollback means switching readers/writers back to legacy adapters and restoring the
      pre-refactor snapshot only if additive changes somehow corrupted existing state.
      A normal code rollback should not require restoring data.
- [x] Never run the live replacement path in `unpack_data.sh` unless the current live
      dataset has first been snapshotted and the exact target has been confirmed.
- [x] After cutover, run:

      ```bash
      make doctor
      .venv/bin/python -m mkb.cli reconcile
      make check
      ```
      All three checks passed after the additive repair and full restore drills.

- [x] Retain the pre- and post-migration snapshots until at least one complete local work
      cycle has succeeded: ingest, process, extract, project, graph/workflow operations,
      review, query, and export.
      The retained live records were exercised across every listed read/export path by
      the 12 ID mappings and 45 payload comparisons; new write/resume behavior passed
      through the installed-wheel custom pipeline. Both drilled snapshots remain stored.

## Phase 9 — Packaging and distribution

- [x] Keep the base wheel lightweight and provide optional extras, for example:
  - `mat-know-base[postgres]`
  - `mat-know-base[s3]`
  - `mat-know-base[pdf]`
  - `mat-know-base[neo4j]`
  - `mat-know-base[server]`
  - `mat-know-base[materials]`
  - `mat-know-base[all]`
- [x] Ensure `pip install mat-know-base` supports a minimal SQLite + filesystem example
      without Docker, PostgreSQL, MinIO, FastAPI, React, or MinerU.
- [x] Keep Alembic resources and built-in pipeline/schema assets inside the wheel and
      resolve them with `importlib.resources`, not the current working directory.
- [x] Remove assumptions that `config.yaml`, `.env`, `alembic.ini`, `data/`, or the repo
      root exists beside the installed package.
- [x] Add versioned database compatibility metadata and refuse to open a database newer
      than the installed library understands.
- [x] Adopt semantic versioning, a deprecation policy, a public API compatibility test,
      changelog, and migration guide.
- [x] Test wheel and source distribution installation in clean environments for the
      minimum and supported Python versions.
      Final wheel and sdist candidates installed with base dependencies only and passed
      the external portable quickstart in clean Python 3.10 and 3.12 environments.
- [x] Publish release candidates locally first and install the built wheel into a
      separate external example project before publishing publicly.
      Local wheel/sdist candidates were rebuilt and installed outside the repository;
      Python 3.10, 3.11, and 3.12 local builds/installs passed, with a 3.10/3.12 hosted
      distribution matrix retained for continuous enforcement.

## External example repository acceptance test

Before declaring the reusable SDK ready, a project depending only on the built wheel
must be able to:

- [x] Create a new SQLite/filesystem knowledge base.
- [x] Connect to the existing local PostgreSQL/MinIO knowledge base and read all current
      extracted data without changing it.
- [x] Create a separate database with no state leaking between the two clients.
- [x] Register a custom source type, parser, schema, and at least two custom pipeline
      steps.
- [x] Ingest arbitrary file, text, and structured-record data.
- [x] Run a custom pipeline and persist structured records, evidence, and graph relations.
- [x] Query, inspect, and export results using only public imports.
- [x] Resume or retry an interrupted pipeline without duplicating outputs.
- [x] Run without importing `mkb.db`, `mkb.web`, ORM models, service-private modules, or
      repository source files.

## Definition of done

- [x] The current materials application, CLI, HTTP API, and React UI work through the new
      application services.
- [x] Current local data is fully readable and usable; no required re-extraction is
      necessary.
- [x] Pre- and post-migration snapshots both pass restore drills.
- [x] Inventory counts, identifier mappings, object references, and representative
      content checks pass.
- [x] Two independently configured knowledge bases work in one process.
- [x] An external project can define and run a custom pipeline using only the installed
      public package.
- [x] The old facade has either full compatibility coverage or a documented, tested
      deprecation path.
- [x] No destructive cleanup of legacy data is required for the first stable SDK release.
