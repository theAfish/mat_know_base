# Architecture and ownership

This map names the main ownership areas in the repository so refactors can move
code toward clearer boundaries without changing behavior accidentally.

## Runtime Surfaces

- `src/mkb/api.py` is the Python compatibility facade used by scripts, tests,
  routers, and legacy UI code.
- `src/mkb/web/` owns the FastAPI app, REST routers, request/response models,
  upload handling, and background job state.
- `frontend/src/` owns the React application.
- `src/mkb/agents/` owns agent construction, prompts, tool adapters, and runner
  integration.
- `src/mkb/cli.py` owns command-line entry points and should call service/API
  functions rather than duplicating behavior.

## Domain Areas

- Ingestion: `src/mkb/ingest/`, `src/mkb/api.py`, and upload entry points under
  `src/mkb/web/`.
- Processing: `src/mkb/processors/`, processed asset models, and S3 helpers.
- Projects and assets: `ResearchProject`, `Asset`, and `ProjectAsset` models,
  plus project routers and frontend project views.
- Frames: `KnowledgeFrame` storage, frame agent code, frame routers, and React
  frame/project detail tabs.
- Spaces and projections: `src/mkb/spaces/`, projection agents/tools, projection
  routers, and projection table components.
- Workflows: `src/mkb/services/workflows/`, workflow extraction/canonicalization agents,
  schema curator tools, and workflow tabs.
- Knowledge graph: `src/mkb/knowledge_graph.py`, graph agent/tools, graph review
  tools, graph router, and graph frontend page.
- Feedback: `src/mkb/feedback/`, feedback agent/tools, feedback router, and
  frontend feedback page.
- Jobs: `src/mkb/web/_state.py`, job routers, and job polling hooks/stores.

## Boundary Direction

Adapters should stay thin:

- Web routers validate HTTP input and map service errors to HTTP responses.
- CLI commands parse arguments and print results.
- Agent tools parse tolerant user/agent inputs and call strict domain helpers.
- React components call typed client functions and avoid backend policy logic.

Shared behavior belongs in domain services or pure helpers before it is reused
by routers, CLI commands, agent tools, and legacy compatibility surfaces.

## Review ownership

`CODEOWNERS` records the enforceable GitHub review routing. Changes under
`src/mkb/db/` need database review. Authentication,
uploads, executable post-processors, deployment configuration, Compose exposure, and
security documentation need security review. Until dedicated teams exist, the
repository owner fills both roles; split these entries into teams as maintainership
grows.

Cross-boundary changes should name the owning service in the pull request and keep
transport/UI adapters free of duplicated domain policy. API compatibility changes also
require updates to `docs/python-api.md` or `docs/api-contract.md` as applicable.
