# MKB Refactoring and Operational Safety TODO

Review date: 2026-07-17

Goal: make MKB clean to develop, predictable to maintain, and safe to operate.

## Current baseline

- Python: Ruff passes; 155 tests pass with upstream deprecation warnings.
- Frontend: TypeScript checking and the production build pass.
- Repository: the working tree was clean at the start of this review.
- Architecture: the backend has already been split into services and routers, but
  several large agent-tool and React modules remain.
- Operating assumption: treat the current application as **trusted, single-user,
  localhost-only software**. Do not expose it to a LAN or the public internet until
  the P0 security boundary is complete.

## P0 - Establish a safe operating boundary

- [x] Add an explicit deployment mode and fail closed outside local development.
  - Define `development`, `local`, and `production` behavior in one settings model.
  - Keep the API bound to `127.0.0.1` by default.
  - Refuse production startup when default credentials, wildcard origins, debug
    logging, or missing authentication are detected.
  - Done when startup tests cover safe defaults and every unsafe override produces a
    prominent warning or hard failure, as appropriate.

- [x] Add authentication and authorization before supporting remote access.
  - Protect all `/api` routes except liveness/readiness checks.
  - Separate read, mutate, destructive, settings, and code-upload permissions.
  - Apply CSRF protection if browser cookie authentication is used.
  - Add rate limits for login, upload, assistant, and job-start endpoints.
  - Done when an unauthenticated client cannot read source assets, mutate data,
    start costly jobs, change settings, or upload executable content.
  - Implemented with header-based bearer tokens and reader/editor/admin roles;
    cookie authentication and CSRF are not used. Failed authentication, upload,
    assistant, and job-start requests have explicit per-process rate limits.

- [x] Replace wildcard CORS with a configured allowlist.
  - `src/mkb/web/api_server.py` currently combines `allow_origins=["*"]`,
    credentials, all methods, and all headers.
  - Default to the exact local frontend origin and validate configured origins.
  - Add API tests for allowed and rejected preflight requests.

- [x] Stop publishing development data services on every network interface.
  - Bind PostgreSQL, MinIO, and the MinIO console to `127.0.0.1` in Compose.
  - Read credentials from `.env`; remove fixed credentials from Compose and bucket
    initialization commands.
  - Pin PostgreSQL/pgvector, MinIO, and `mc` images to tested versions or digests.
  - Document that default credentials are disposable local-development values only.

- [ ] Isolate or disable uploaded Python post-processors by default.
  - Uploaded `.py` files currently run with the API process's Python interpreter,
    environment, filesystem access, and network access.
  - Short term: require an explicit trusted-admin opt-in and show the risk in the UI.
  - Long term: execute in a constrained worker/container with a read-only root,
    minimal mounted input, no inherited secrets, disabled network, CPU/memory/PID
    limits, output limits, and a hard timeout.
  - Validate the patch schema and cap stdout/stderr before returning errors.
  - Done when a test script cannot read `.env`, contact the network, modify project
    files, or exhaust host resources.
  - Progress: upload and execution now require the environment-only trusted-admin
    opt-in; output and patch shapes are capped/validated. Worker isolation remains.

- [ ] Put hard resource budgets on every upload and archive expansion path.
  - Enforce request, per-file, total-upload, file-count, filename-depth, and timeout
    limits while streaming—not after writing to disk.
  - For ZIP/TAR and skill archives, limit compressed size, expanded size, member
    count, per-member size, nesting depth, and compression ratio.
  - Continue rejecting traversal, absolute paths, links, devices, and special files.
  - Garbage-collect abandoned upload sessions and partial files.
  - Add regression tests for zip bombs, nested archives, duplicate names, truncated
    uploads, and quota cleanup.
  - Progress: streaming per-file/session limits and ZIP/TAR/skill expansion limits
    are enforced with partial-file cleanup. Request timeouts, abandoned-session GC,
    and scheduled quota cleanup remain.

- [ ] Protect secrets and sensitive research content in settings and logs.
  - Store runtime secrets outside a general JSON settings file, or use a system
    secret store; at minimum write atomically with owner-only permissions.
  - Redact credentials, authorization headers, signed URLs, prompts, source content,
    and model payloads from logs by default.
  - Default to `INFO`; do not always capture the full DEBUG stream on disk.
  - Stop truncating logs at every startup; retain them according to an explicit
    size/age policy.
  - Add redaction tests using recognizable fake secrets.
  - Progress: runtime settings are replaced atomically with owner-only permissions;
    INFO is now the default; rotating logs append across restarts; and recognizable
    credentials, authorization headers, URL credentials, and signed URL parameters
    are redacted. A system secret store and content-aware redaction remain.

- [ ] Add safeguards and audit records for destructive operations.
  - Inventory project, projection, workflow, graph, group, skill, and script deletes,
    plus database reset and snapshot restore.
  - Require confirmation or a typed resource identifier for bulk/destructive CLI
    actions; require elevated authorization in the API.
  - Prefer soft delete plus a documented recovery window where practical.
  - Record actor, action, target, timestamp, request/job ID, and outcome without
    logging sensitive payloads.

## P1 - Make data and background work recoverable

- [x] Make Alembic the only schema migration mechanism.
  - `mkb.db.engine` currently mixes `create_all()` with handwritten compatibility
    DDL, which can hide migration drift.
  - Convert compatibility changes into reviewed migrations and verify one linear
    head from an empty database and from a supported older snapshot.
  - Make startup check the schema revision and fail with an actionable message.
  - Add upgrade and downgrade/forward-recovery tests in disposable PostgreSQL.
  - Implemented: revision `003` now contains the reconstructed initial schema,
    revision `0022_schema_drift_cleanup` captures the last compatibility changes,
    runtime schema mutation was removed, and startup checks the single head. Empty,
    downgrade/forward, and base/forward drills pass against disposable PostgreSQL
    with no ORM migration drift.

- [x] Replace in-memory threads and job dictionaries with a durable job model.
  - Persist queued/running/terminal state, progress, cancellation, attempt count,
    timestamps, and idempotency keys.
  - Define restart behavior: safely resume retryable jobs and mark interrupted jobs
    explicitly instead of losing them.
  - Enforce per-action concurrency and prevent duplicate work across processes.
  - Keep cooperative cancellation, but add checkpoints around long LLM, database,
    processor, and storage operations.
  - Implemented with PostgreSQL-backed job/event/result records, request and
    idempotency keys, database-enforced active-work locks, persisted cooperative
    cancellation, attempt/timestamp fields, and explicit restart interruption.

- [x] Define transaction and idempotency boundaries for every workflow.
  - Document which database and S3 writes constitute ingest, process, extract,
    project, review, and delete completion.
  - Use staging keys/statuses and compensating cleanup so partial failures are
    visible and retryable.
  - Add failure-injection tests between database commits and object-store writes.
  - Documented completion, retry identity, staging, and compensation boundaries
    for ingest, process, extract, project/review, and delete. Reconciliation makes
    incomplete cross-store writes visible and retryable job locks prevent duplicate
    workflow execution.

- [x] Turn snapshots into a tested backup and restore procedure.
  - Make snapshot creation fail if any PostgreSQL/MinIO copy step fails; remove
    `|| true` from integrity-critical commands.
  - Validate archive paths before extraction and avoid interpolating credentials or
    paths into shell/Python source strings.
  - Add a versioned manifest, checksums, schema revision, application version, and
    optional encryption.
  - Restore into staging first, validate it, then require explicit replacement.
  - Run a documented restore drill against a disposable environment in CI or on a
    schedule.
  - Implemented strict copy failures, pinned tools, safe extraction, a versioned
    checksummed manifest, schema/application versions, optional age encryption,
    disposable-database validation, typed replacement confirmation, and a reusable
    restore-drill target.

- [x] Add meaningful liveness, readiness, and diagnostics.
  - Keep liveness process-only.
  - Readiness must verify database connectivity/revision, required S3 buckets, and
    worker availability without exposing secrets.
  - Add structured request/job IDs and useful error categories.
  - Document a short operator runbook for startup, shutdown, stuck jobs, full disks,
    provider outages, backup, restore, and upgrade.
  - Progress: process-only liveness and non-sensitive readiness checks now cover
    database connectivity/revision, required S3 buckets, and local worker
    availability. Request/job ID propagation and the operator runbook remain.
  - Completed with validated request-ID propagation into durable jobs and responses,
    categorized diagnostics, structured request logging, and an operator runbook.

- [x] Add retention and reconciliation commands.
  - Provide dry-run cleanup for stale upload sessions, processor temp files, local
    mirrors, exports, logs, job history, and orphaned S3/database records.
  - Require explicit confirmation before deletion and report reclaimed bytes/items.
  - Add a read-only consistency checker for PostgreSQL, MinIO, and local metadata.
  - Implemented dry-run-first local/job retention with typed confirmation and
    reclaimed counts, plus read-only database/S3 missing-and-orphan reporting.

## P1 - Restore one source of truth at boundaries

- [ ] Finish the service result/error migration.
  - Replace remaining `{"error": ...}` success-shaped dictionaries with typed
    exceptions/results.
  - Map domain errors once in REST, CLI, and agent adapters.
  - Define stable error codes; do not make callers parse human-readable messages.

- [ ] Generate and validate frontend API contracts.
  - Export OpenAPI in a deterministic build step and generate TypeScript types, or
    validate high-risk payloads with shared/generated Zod schemas.
  - Cover projects, jobs, workflows, projections, spaces, settings, and errors.
  - Fail CI when generated contracts are stale.

- [ ] Standardize identifiers, timestamps, enums, and pagination.
  - Use strict UUID parsing internally and tolerant parsing only in agent-tool
    adapters where it is intentional.
  - Use UTC ISO-8601 consistently and define enum casing once.
  - Add bounded pagination to collection/search endpoints instead of returning
    unbounded lists.

- [ ] Add typed contracts for important JSONB payloads.
  - Prioritize knowledge-frame content metadata, workflow graphs/checkpoints,
    projection data/review patches, job results/events, provenance, and space
    schemas.
  - Validate before persistence and provide versioned migrations for payload-shape
    changes.

- [ ] Consolidate processed-bundle metadata and hashing.
  - Use one model for automatic processing, manual processed uploads, artifact
    inspection, primary-file selection, checksums, and S3/local paths.
  - Make reprocessing idempotent and retain provenance for derived artifacts.

## P2 - Reduce architectural duplication

- [ ] Finish merging the two project-detail experiences.
  - Consolidate `components/projects/ProjectDetail.tsx` and
    `components/frames/ProjectDetail.tsx` around shared action, status, tab, and
    refresh components.
  - Route job state through one store and one polling/subscription layer.

- [ ] Split the largest React features along domain boundaries.
  - Start with `SpacesPage.tsx`, `GraphPage.tsx`, `ProjectionsPage.tsx`,
    `ProjectGroupedList.tsx`, `WorkflowCanvas.tsx`, and `SectionTable.tsx`.
  - Extract pure transformations first, then presentation components, then thin
    route containers.
  - Keep feature-specific API, schemas, tests, and components together.

- [ ] Finish frontend code splitting and set bundle budgets.
  - The current build still reports chunks over 500 kB for frames and graph
    visualization, plus a roughly 1.2 MB PDF worker.
  - Lazy-load PDF, graph, workflow, and large table functionality only when opened.
  - Track compressed route/chunk budgets in CI.

- [ ] Remove the legacy Streamlit surface or move it to a separately installed
  compatibility package.
  - Stop testing new behavior through `mkb.ui` helpers.
  - Move shared upload/project-name logic into backend domain helpers.
  - Remove Streamlit and visualization packages from default dependencies when the
    compatibility surface is retired.

- [ ] Split large agent-tool modules into query, validation, mutation, and
  persistence layers.
  - Prioritize projection, schema curator, graph review, knowledge graph, and
    canonicalization tools.
  - Agent tools should validate tool-shaped input and delegate; they should not own
    transaction-heavy business rules.

- [ ] Centralize graph and projection normalization rules.
  - Put deduplication, aliases, relation validation, merge policy, patch/path
    operations, and source-evidence preservation behind domain services.
  - Add small regression fixtures for same-paper duplicates, cross-paper aliases,
    conflicting values, repeated reviews, and source preservation.

- [ ] Complete the canonical-workflow retirement.
  - Remove deprecated launch paths, frontend tabs, agent modules, schema tables, and
    dependencies after an explicit export/migration window.
  - Keep compatibility reads isolated and time-boxed if existing datasets need them.

## P2 - Improve testing and delivery

- [ ] Add CI for every pull request and protected branch.
  - Run Python lint, tests, and migration checks; frontend type-check, tests, and
    build; plus secret, dependency, and container scans.
  - Use dependency caches and cancel superseded runs.
  - Require the workflow before merge.

- [ ] Add frontend behavioral tests.
  - Use a unit/component runner for stores, schemas, polling, upload grouping,
    projection editing, and error handling.
  - Add a small browser smoke suite for upload -> process -> extract/project status,
    cancellation, settings, and destructive confirmations.

- [ ] Add real integration tests with PostgreSQL and MinIO.
  - Cover migrations, ingest deduplication, object cleanup, failure rollback,
    archive limits, auth, CORS, job restart behavior, and backup/restore.
  - Keep LLM and MinerU network calls deterministic behind fakes; maintain a small
    opt-in end-to-end provider smoke test.

- [ ] Strengthen quality tooling.
  - Add ESLint and a formatter; the current frontend `lint` script is TypeScript
    checking only.
  - Add Python formatting, import, security, and type-check policies incrementally.
  - Track coverage by critical domain rather than chasing one global percentage.

- [ ] Pin and automate dependency maintenance.
  - Establish a reproducible Python lock/constraints workflow; keep the npm lockfile.
  - Separate runtime, local-PDF, legacy-UI, and development dependency groups.
  - Automate reviewed update pull requests and vulnerability/license checks.

## P3 - Developer experience and documentation

- [ ] Make every command use the project environment consistently.
  - The Makefile currently mixes ambient `python`, `pytest`, `alembic`, and `pip`
    with `.venv/bin/python`.
  - Introduce one configurable Python command and use `python -m ...` consistently.
  - Make setup, migrate, lint, test, build, and dev commands work from a clean clone.
  - Progress: Make targets now use one configurable `PYTHON` and `python -m ...`.
    Clean-clone environment creation still needs a bootstrap target.

- [ ] Make `make up` wait for health and run explicit migrations safely.
  - Do not hide readiness failures with `|| true`.
  - Provide `make doctor` to check Python/Node/Docker versions, configuration,
    ports, database revision, buckets, disk space, and external processors.
  - Progress: `make up` now uses Compose health waiting and runs Alembic without
    suppressing failures. `make doctor` remains.

- [ ] Reorganize documentation around user roles.
  - Keep README as the fast local quickstart.
  - Add developer setup, architecture/ownership, API contract, security model,
    operator runbook, backup/restore, upgrade/migration, and contribution guides.
  - Clearly label React as current and Streamlit/canonical workflows as legacy.

- [ ] Add repository policy files.
  - Add `CONTRIBUTING.md`, security reporting guidance, supported-version policy,
    pull-request template, and ownership/review rules for migrations and security
    sensitive code.

## Recommended implementation order

1. Safe local-only defaults: loopback binds, CORS allowlist, production startup
   checks, secret/log redaction, and upload quotas.
2. Disable or isolate uploaded code; add authentication before any remote use.
3. Migration-only schema management, durable jobs, transaction/idempotency rules,
   and tested backup/restore.
4. CI plus API/frontend/integration contracts and tests.
5. Remove legacy surfaces and split the largest frontend and agent-tool modules.
6. Finish documentation, dependency separation, and routine operator tooling.

## Definition of done for this program

- A clean clone can be installed, migrated, checked, and started using documented
  commands without relying on hidden global tools.
- Default services are reachable only from localhost and use no production-unsafe
  defaults silently.
- Remote deployment has authentication, least-privilege authorization, bounded
  uploads/jobs, and isolated executable extensions.
- Interrupted workflows and partial storage writes are visible, retryable, and
  reconcilable.
- Backup restoration is tested, not assumed.
- API contracts, migrations, Python tests, frontend tests/build, and security checks
  are required in CI.
- Each major domain has a clear owner module; REST, CLI, agent, and UI layers are
  adapters rather than competing implementations of business logic.
