# Python API

`KnowledgeBase` is the new explicit entry point for Python callers. During the SDK
refactor it delegates to the same services and reads the same PostgreSQL and MinIO data
as the current application; creating it does not migrate, copy, or re-extract data.

```python
from mkb import KnowledgeBase

with KnowledgeBase.from_environment() as kb:
    projects = kb.list_projects(limit=100)
    frames = kb.list_frames(status="COMPLETED")
```

`mkb.api` remains the supported compatibility facade while feature parity is built.
Existing automation does not need to change yet. Import from one of these public
surfaces, not from database models, web routers, or individual service modules. Both
surfaces are synchronous and currently return JSON-compatible dictionaries and lists
(UUIDs and timestamps may still be native Python values in some records).

```python
from mkb import api
```

The explicit client is preferable for new code because its configuration and service
bindings belong to one object rather than module globals. The initial environment
adapter still uses the existing application services; independently configured SQLite,
PostgreSQL, filesystem, S3, and graph adapters will be added incrementally.

The API performs real database, object-storage, filesystem, processor, and LLM work.
It is not an in-memory SDK. Configure `.env`, start infrastructure with `make up`, and
run calls from the repository root so `config.yaml` and `alembic.ini` are found.

## End-to-end lifecycle

```python
from pathlib import Path
from mkb import api

api.setup()  # idempotently upgrades the configured database to Alembic head

created = api.ingest(Path("data/papers/smith2024"), label="Smith 2024")
project_id = created["project_id"]

processed = api.process(project_id=project_id)
extracted = api.extract(project_id=project_id, max_passes=2)
frame = api.get_frame(project_id)
if frame is None:
    raise RuntimeError("extraction did not create a frame")

space = api.create_space(
    name="Synthesis conditions",
    domain="materials science",
    description="Experimental synthesis facts",
    extraction_schema={
        "type": "object",
        "properties": {
            "material": {"type": "string"},
            "temperature_c": {"type": ["number", "null"]},
        },
        "required": ["material"],
    },
    field_descriptions={"temperature_c": "Reported synthesis temperature in Celsius"},
    system_prompt="Extract only values supported by the project evidence.",
)
projection = api.project(space_id=space["space_id"], project_id=project_id)

print(processed, extracted)
print(api.get_projection(projection["projection_id"]))
```

Most mutating functions return a summary dictionary containing stable identifiers and
counts. Read functions return a dictionary, a list of dictionaries, or `None` when a
singular resource does not exist. Invalid IDs, missing prerequisites, storage errors,
and provider failures raise exceptions; library callers should catch exceptions at a
job or request boundary rather than infer success from partial output.

## Setup, ingest, and assets

| Call | Purpose and important arguments |
| --- | --- |
| `setup()` | Upgrade the configured database through Alembic. Safe to repeat. |
| `reset_db()` | Drop and recreate all tables. Destructive; the Python call has no interactive confirmation. |
| `ingest(directory, label=None, *, user_named=False)` | Create or update one project from a directory and upload source assets. The directory must exist. |
| `sync(root_dir)` | Treat each immediate project folder below a root as a project and rescan it. |
| `sync_project(project_id)` | Rescan the source directory recorded for one project. |
| `process(project_id=None, progress_callback=None)` | Process pending assets for one project or all projects. |
| `list_assets(project_id=None, limit=100)` | Return source asset metadata. |
| `list_processed_assets(project_id=None, limit=100)` | Return derived artifact metadata. |
| `search_library(query, limit=25, project_id=None)` | Search project and asset metadata; returns `projects`, `assets`, and `total`. |

`progress_callback`, where accepted, is called during long-running synchronous work.
Callbacks should be fast, thread-safe if the caller adds concurrency, and tolerate
repeated/non-uniform progress payloads. The stable contract is notification, not a
fixed event schema.

For externally prepared output, use
`link_manual_processed_data(processed_dir, paper_dir=None, project_id=None,
asset_id=None, primary_file=None, processing_type=None, output_format=None)`. Supply
an `asset_id` when possible; otherwise provide enough project/paper context to select
one asset unambiguously.

## Projects and frames

```python
projects = api.list_projects(limit=100)
api.rename_project(project_id, "New label")
assets = api.list_assets(project_id=project_id)

api.extract(project_id=project_id, model=None, verbose=False, max_passes=2)
frame = api.get_frame(project_id)             # dict | None
history = api.get_extraction_history(project_id)
completed = api.list_frames(status="completed")
```

Project group calls are `list_project_groups()`, `create_project_group(name, ...)`,
`update_project_group(group_id, ...)`, `assign_projects_to_group(project_ids,
group_id)`, and `delete_project_group(group_id)`. Passing `None` as the assignment
group removes the projects from a group. `delete_project(project_id,
delete_s3_objects=True)` is a hard delete and should be guarded by application-level
confirmation.

## Spaces and projections

`create_space(...)` accepts a JSON Schema-like `extraction_schema`, system prompt,
optional field descriptions, purpose, review settings, search tools, and
post-processors. `update_space(space_id, **changes)` increments its version.
`get_space(id_or_name)` accepts either identifier form; delete calls require an ID.

Projection calls:

```python
api.project(space_id, frame_id=None, project_id=None, model=None,
            verbose=False, progress_callback=None, source_type="frame")
api.project_all(space_id, model=None, verbose=False, source_type="frame")
api.list_projections(space_id=None, frame_id=None, project_id=None,
                     include_data=False, newest_only=False, include_history=False)
api.get_projection(projection_id)
api.delete_projection(projection_id)  # soft delete; returns bool
api.export_projection(projection_id, out_dir, format="yaml", overwrite=False)
api.export_space_projections(space_id_or_name, out_dir, format="yaml",
                             overwrite=False, newest_only=True)
```

Provide exactly the source selector appropriate to `source_type`; the normal current
path is `source_type="frame"` with `project_id` or `frame_id`. Exports refuse to
overwrite by default. Space updates can make older projections historical, so use
`newest_only=True` when producing a current dataset.

## Knowledge graph and feedback

The graph API exposes `extract_knowledge_graph`, `get_knowledge_graph`,
`review_knowledge_graph`, `get_graph_review_counts`, and `clear_knowledge_graphs`.
Extraction can clear existing graph projections by default; set arguments deliberately
when preserving prior results. Clearing is a mutating soft-delete operation.

Feedback calls include `list_feedback`, `get_feedback_summary`, `resolve_feedback`,
`review_feedback`, `review_projections`, `review_projections_all`,
`review_projections_session`, and `review_projection_followup`. Review functions may
invoke an LLM and mutate projections or feedback, so record the chosen model and
reviewer ID in reproducible automation.

## Workflow API and compatibility status

Raw workflow extraction/review is active: `extract_raw_workflow`,
`get_raw_workflow_extraction_readiness`, `list_raw_workflows`, `get_raw_workflow`,
`review_raw_workflow`, and `correct_raw_workflow`. Schema and maintenance calls include
`curate_workflow_schema`, proposal list/edit/review calls,
`schedule_workflow_reextraction`, `list_workflow_maintenance_tasks`,
`run_workflow_maintenance_task`, `rebuild_workflow_indexes`, and
`search_canonical_workflows`.

Canonical workflow records and serializers remain available for compatibility, but
canonicalization is not the target for new product workflows. Consult the
[workflow lifecycle policy](workflow-lifecycle-policy.md) before adding dependencies
on those calls.

## Concurrency, transactions, and compatibility

Calls are synchronous; use a process/job boundary for long processing and agent work.
Do not share SQLAlchemy sessions across threads. Functions establish their own service
boundaries, but a sequence of separate API calls is not one atomic transaction. Design
retries around stable project/asset IDs and inspect current state before repeating
destructive or LLM-backed operations. See [transaction boundaries](transactions.md).

Public names listed in `mkb.api.__all__` are the compatibility surface. Keys in result
dictionaries are less strictly versioned than function names; consumers should read
needed keys and tolerate additive fields. Private names beginning with `_`, ORM models,
and service internals are not supported API even if importable.
