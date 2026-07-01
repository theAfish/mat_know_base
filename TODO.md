# mat_know_base Refactor and Enhancement TODO

Repo review date: 2026-06-30.

This TODO is based on a read-through of the backend API, web routers, agent tools,
workflow modules, React frontend, tests, and dev scripts. It focuses on reducing
duplicate logic, clarifying ownership boundaries, and making the project easier to
operate and develop.

## P0 - Fix Current Dev/Test Breakages

- [x] Restore pytest collection.
  - Current venv command: `.venv/bin/python -m pytest --collect-only -q`.
  - Current failure: `tests/test_projection_utils.py` imports
    `_filter_latest_projections` from `src/mkb/ui/pages/projections.py`, but that
    helper is no longer present.
  - Decide whether to reintroduce the helper, move the test to the React/API
    projection path, or delete the legacy Streamlit-specific assertion.

- [x] Fix Ruff baseline so CI can be meaningful.
  - Current command: `.venv/bin/python -m ruff check src tests`.
  - Current issues include unused imports, ambiguous loop variable `l`, and real
    undefined test variables in `tests/test_workflow_resume.py`.
  - Add a CI command that runs Ruff and pytest collection at minimum.

- [x] Make test commands work without relying on an editable install in a hidden
  local venv.
  - `python3 -m pytest --collect-only -q` fails with `ModuleNotFoundError: mkb`
    on the system interpreter.
  - Options: document `.venv/bin/python -m pytest`, add `pythonpath = ["src"]`
    to pytest config, or standardize on `pip install -e ".[dev]"`.
  - Added pytest `pythonpath = ["src"]`; the system interpreter now reaches repo
    modules but still needs project dependencies installed.

## P1 - Split the Backend API Monolith

- [x] Break up `src/mkb/api.py`.
  - It is currently about 3,000 lines and owns ingestion, processing, frames,
    project groups, deletion, workflow extraction, workflow schema review,
    projection, export, graph, and feedback.
  - Suggested service modules:
    - `mkb.services.projects`
    - `mkb.services.assets`
    - `mkb.services.processing`
    - `mkb.services.frames`
    - `mkb.services.workflows`
    - `mkb.services.projections`
    - `mkb.services.graphs`
    - `mkb.services.feedback`
  - Keep `mkb.api` as a compatibility facade that imports and delegates public
    functions until callers migrate.
  - `mkb.api` is now a compatibility facade over domain modules:
    `mkb.services.runtime`, `ingest`, `assets`, `frames`, `projects`,
    `workflows`, `spaces`, `projections`, `graphs`, and `feedback`.

- [x] Move database serialization helpers next to their domain services.
  - Examples in `src/mkb/api.py`: `_serialize_group`,
    `_serialize_raw_workflow`, `_serialize_canonical_workflow`,
    `_serialize_projection_payload`.
  - This will make router, CLI, API, and agent tool behavior easier to keep
    aligned.

- [x] Create a shared `Result`/error convention.
  - Many API functions return `{"error": ...}` while routers translate those to
    HTTP exceptions manually.
  - Pick a single internal exception/result style and let web, CLI, and agent
    adapters map it to their own surfaces.
  - Added `mkb.services.result.ServiceError`, `error_result`,
    `is_error_result`, and `result_status_code`.
  - Web adapters now use `require_service_result` /
    `require_service_result_or_not_found` instead of hand-unpacking
    `{"error": ...}` dictionaries.
  - Legacy `{"error": ...}` returns remain supported so service modules can
    migrate incrementally.

## P1 - Consolidate Duplicate Web, CLI, and Agent Adapters

- [x] Introduce a single job action registry.
  - Duplicate job-starting logic exists in:
    - `src/mkb/web/routers/projects.py`
    - `src/mkb/web/_state.py`
    - `src/mkb/agents/tools/orchestrator_tools.py`
    - Streamlit background job paths under `src/mkb/ui`
  - Define one table of job kinds, labels, target functions, validation, and
    active-job conflict policy.
  - Use that registry for REST routes, assistant-triggered workflows, batch
    actions, and any remaining Streamlit actions.
  - Done: `mkb.web.job_actions` now defines action metadata, target lookup,
    argument validation, and active-job conflict policy for REST, assistant
    workflow dispatch, batch actions, upload ingest, and legacy Streamlit
    project actions.

- [x] Replace forced thread cancellation in `JobManager`.
  - `src/mkb/web/_state.py` uses `ctypes.pythonapi.PyThreadState_SetAsyncExc`.
  - This can interrupt database sessions, file writes, S3 operations, or agent
    tool calls at unsafe points.
  - Prefer cooperative cancellation through `progress_callback`, cancellation
    tokens, and explicit checks in long-running loops.

- [x] Extract upload/archive handling from `src/mkb/web/api_server.py`.
  - The file notes that upload logic is inline for test monkeypatch compatibility.
  - Move implementation to `mkb.web.uploads` and re-export wrapper functions in
    `api_server.py` so tests and callers retain the same patch points.

- [x] Centralize preview/content response logic.
  - `src/mkb/web/routers/projects.py` owns `_inline_headers` and
    `_asset_media_type`.
  - Move content negotiation, safe filename headers, and S3 download response
    creation into a shared web helper before adding more preview types.

## P1 - Clarify the Workflow Migration Boundary

- [x] Decide whether canonical workflows are legacy, active, or compatibility-only.
  - `docs/workflow-card-architecture.md` says extraction to canonicalization is
    retired.
  - The code still exposes canonical workflow contracts, API functions, router
    endpoints, frontend tabs, maintenance tasks, and tests.
  - Document the current policy and mark each public endpoint as active,
    deprecated, or internal compatibility.
  - Documented in `docs/workflow-lifecycle-policy.md`.

- [x] Group workflow code by lifecycle.
  - Current workflow behavior spans:
    - `src/mkb/workflows/*`
    - `src/mkb/agents/workflow_extraction.py`
    - `src/mkb/agents/workflow_canonicalization.py`
    - `src/mkb/agents/schema_curator.py`
    - `src/mkb/agents/tools/workflows.py`
    - `src/mkb/agents/tools/workflow_canonicalization.py`
    - `src/mkb/agents/tools/schema_curator.py`
    - many sections of `src/mkb/api.py`
  - Create a workflow service package with explicit submodules for extraction,
    validation, schema review, indexing, and legacy canonicalization.
  - Done: `mkb.services.workflows` is now a lifecycle package with
    `extraction`, `serialization`, `schema_review`, `maintenance`, `indexing`,
    and `legacy_canonicalization` modules. Active routes/jobs use raw
    workflow extraction and review; canonicalization launch is no longer part
    of the active REST/job flow.

- [x] Remove duplicated schema/card operations between
  `src/mkb/agents/tools/workflows.py` and
  `src/mkb/agents/tools/workflow_canonicalization.py`.
  - Both modules normalize payloads, expose card/template operations, and
    manipulate draft graphs or schema libraries.
  - Done: shared card search, schema-library views, raw graph normalization,
    checkpoint manifests, compacting, and draft replacement helpers live in
    `mkb.workflows.editing`. Agent tools now call that library instead of
    owning duplicate low-level operations.

## P1 - Reduce Frontend Duplication

- [ ] Merge the two project detail experiences.
  - `frontend/src/components/projects/ProjectDetail.tsx`
  - `frontend/src/components/frames/ProjectDetail.tsx`
  - Both own project actions, job polling, space selection, graph/workflow
    display, and project refresh logic.
  - Extract shared hooks/components:
    - `useProjectActions`
    - `useProjectRefresh`
    - `ProjectActionBar`
    - `ProjectStatusHeader`
    - `ProjectTabs`
  - Started: extracted `useProjectRefresh`; action bar/status/tabs are still
    duplicated.

- [ ] Split large React pages into feature modules.
  - Biggest current files:
    - `frontend/src/pages/SpacesPage.tsx` at about 1,400 lines
    - `frontend/src/components/projects/WorkflowCanvas.tsx` at about 1,100 lines
    - `frontend/src/pages/GraphPage.tsx` at about 900 lines
    - `frontend/src/components/projections/SectionTable.tsx` at about 700 lines
  - Prioritize extracting pure transformation helpers first, then reusable
    controls, then page-level containers.

- [ ] Finish job polling consolidation.
  - `frontend/src/api/jobPolling.ts` is a good shared primitive.
  - Continue removing local `pollJob`, `refreshOnFinishedJob`, and manual job
    list merging patterns from page components.
  - Route all job updates through `frontend/src/store/jobsStore.ts` unless a
    component truly needs isolated state.

- [x] Add route-level code splitting.
  - `npm run build` succeeds, but Vite reports a large JS chunk around 1.6 MB.
  - Lazy-load heavy pages/components such as graph visualization, PDF preview,
    projections table, workflow canvas, and skills/settings pages.

## P2 - Remove Legacy Streamlit Surface or Fence It Off

- [x] Decide the long-term owner for `src/mkb/ui`.
  - README says React replaces the legacy Streamlit UI, but tests and modules
    still import Streamlit page helpers.
  - Either:
    - remove Streamlit pages after migrating tests and any missing behavior, or
    - move them under `mkb.legacy_ui` and mark as compatibility-only.
  - Marked `src/mkb/ui` compatibility-only in `src/mkb/ui/README.md`.

- [ ] Stop testing new behavior through Streamlit helper functions.
  - `tests/test_projection_utils.py` and upload grouping tests still target
    `src/mkb/ui/pages/*`.
  - Prefer tests against pure helper modules, backend service functions, or REST
    router behavior.

- [ ] Deduplicate upload grouping behavior between Streamlit and React/API.
  - Similar project naming, collision handling, archive expansion, and grouping
    logic appears in:
    - `src/mkb/ui/pages/projects.py`
    - `src/mkb/web/api_server.py`
    - `frontend/src/components/projects/uploadHelpers.ts`
  - Put backend-safe path/name logic in one Python module and mirror only the UI
    preview heuristics in TypeScript.

## P2 - Improve Type and Schema Contracts

- [ ] Generate or validate frontend API types from backend models.
  - Backend request models live in `src/mkb/web/_models.py`.
  - Frontend domain types live in `frontend/src/types/index.ts`.
  - Add an OpenAPI export and a type generation step, or add zod schemas at the
    client boundary for high-risk payloads.

- [ ] Standardize identifiers at boundaries.
  - UUID parsing is repeated with `_parse_uuid`, `parse_uuidish`, ad hoc
    `uuid.UUID(str(...))`, and frontend string handling.
  - Define per-boundary helpers:
    - web request validation
    - agent tool tolerant parsing
    - internal strict UUID conversion
  - Started: added `mkb.services.ids` and wired web UUID parsing through it.

- [ ] Audit JSON/blob fields in `src/mkb/db/models.py`.
  - Many important states live in `metadata_`, `content`, `data`, `result`,
    `checkpoint`, and `provenance`.
  - Add Pydantic contracts for high-value payloads before they enter the DB,
    especially workflow checkpoints, projection review results, and job results.

## P2 - Processor and Storage Cleanup

- [x] Make processor registration declarative.
  - `src/mkb/processors/coordinator.py` owns a hard-coded `PROCESSORS` list and
    special cases ambiguous text files.
  - Add a registry that can rank processors by MIME type, extension, and content
    sniffing confidence.

- [ ] Reuse bundle hashing and processed-output inspection.
  - `src/mkb/api.py` manually inspects handmade processed directories.
  - `src/mkb/processors/base.py` and `src/mkb/processors/coordinator.py` compute
    processed result hashes and artifact metadata.
  - Extract one processed bundle model/helper so manual and automatic processing
    share the same hashing, artifact list, and primary file rules.

- [ ] Add lifecycle cleanup for local processed/upload temp files.
  - Upload temp folders, processed local mirrors, and generated exports can grow
    quickly during research use.
  - Add commands for dry-run cleanup, retention windows, and orphan detection.

## P2 - Knowledge Graph and Projection Quality

- [ ] Move graph normalization/dedup logic behind a graph service.
  - Logic currently sits in `src/mkb/knowledge_graph.py`,
    `src/mkb/agents/tools/knowledge_graph.py`, and graph review tools.
  - Keep agent tools thin and centralize concept/relation validation,
    deduplication, merge rules, and review counters.

- [ ] Separate projection extraction, review, patching, and export concerns.
  - `src/mkb/agents/tools/projection.py` is over 1,000 lines.
  - `src/mkb/agents/tools/projection_review.py` is another large mixed module.
  - Suggested split:
    - read/query helpers
    - projection mutation helpers
    - patch/path operations
    - review session persistence
    - export formatting

- [ ] Add regression fixtures for duplicate projection and graph merge cases.
  - The product depends heavily on deduplication quality.
  - Keep small fixtures for same-paper repeated projections, cross-paper concept
    aliases, and source-reference preservation.

## P3 - Dev Experience and Repo Hygiene

- [x] Add a `make check` target.
  - Suggested steps:
    - `.venv/bin/python -m ruff check src tests`
    - `.venv/bin/python -m pytest`
    - `cd frontend && npm run build`
  - Add lighter targets for `make lint`, `make test-python`, and
    `make test-frontend`.

- [x] Add frontend linting and formatting.
  - `frontend/package.json` has build scripts only.
  - Add ESLint/Prettier or a minimal TypeScript-aware lint command so React
    cleanup can be enforced incrementally.

- [x] Add a short architecture map.
  - The README is broad and useful, but new developers need a quick owner map:
    ingestion, processing, frames, spaces, projections, graph, workflows, jobs,
    frontend.
  - Put it in `docs/architecture-map.md`.

- [x] Keep generated/local artifacts out of review noise.
  - `.gitignore` covers `data/`, `logs`, `.debug/`, `node_modules/`, and
    `__pycache__/`.
  - Tracked data exports currently include:
    - `data/exports/projections_yaml/*.yaml`
    - `data/inbox/.gitkeep`
  - Decide whether exported projection YAML files should remain tracked
    fixtures or move under examples/fixtures with clear names.
  - Documented generated-output policy in `docs/development.md`.

- [x] Add dependency and environment notes.
  - `python` is not available in this environment, but `python3` and `.venv` are.
  - Make docs and scripts consistently use `python3` or `.venv/bin/python`.
  - Consider pinning high-risk dependencies or adding a constraints file for
    reproducible agent and PDF-processing environments.

## Suggested Refactor Order

1. Fix pytest collection and Ruff baseline.
2. Introduce service modules behind the existing `mkb.api` facade.
3. Centralize background job action registration and remove unsafe thread
   cancellation.
4. Merge frontend project detail/action logic and finish job polling
   consolidation.
5. Formalize workflow canonicalization as active or legacy, then prune or fence
   matching backend/frontend/tests.
6. Split the largest frontend pages and agent tool modules after the service
   boundaries are stable.

## Verification Notes From This Pass

- [x] `python3 -m compileall -q src` passed.
- [x] `cd frontend && npm run build` passed.
- [x] `.venv/bin/python -m pytest --collect-only -q` passed with 111 tests
  collected.
- [x] `.venv/bin/python -m ruff check src tests` passed.
- [x] `.venv/bin/python -m pytest -q` passed with 111 tests.
- [x] `cd frontend && npm run lint` passed.
