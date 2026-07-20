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
surfaces, not from database models, web routers, or individual service modules. The
compatibility facade and legacy methods on `KnowledgeBase` still return dictionaries
and lists; grouped SDK services such as `kb.collections`, `kb.schemas`, and `kb.graph`
return typed public models.

```python
from mkb import api
```

## Public import boundary

The supported root package exports the configured client, immutable configuration,
typed domain and operation models, pipeline/parser definitions, grouped registry
types, transactions, and the public exception hierarchy. All typed models support
`model_dump(mode="json")`.

Custom adapter authors may explicitly import protocols from `mkb.ports` and default
implementations from `mkb.adapters`. Application consumers should not import
`mkb.db`, `mkb.web`, ORM classes, session factories, or storage implementation
modules. The old port aliases remain explicitly importable from `mkb` for temporary
compatibility, but are intentionally absent from `mkb.__all__`.

The explicit client is preferable for new code because its configuration and service
bindings belong to one object rather than module globals. The initial environment
adapter still uses the existing application services; independently configured SQLite,
PostgreSQL, filesystem, S3, and graph adapters will be added incrementally.

For pipeline execution and typed repository access without reading `.env` or YAML,
construct an independent client explicitly:

```python
from mkb import KnowledgeBase

with KnowledgeBase.from_url(
    database_url="sqlite:////absolute/path/project.db",
    object_store_url="file:///absolute/path/objects",
) as kb:
    kb.database.check()
    kb.initialize()  # explicit, idempotent creation of missing SDK-owned tables

    collection = kb.collections.create(name="Experiment 42")
    kb.records.create(
        collection_id=collection.id,
        data={"material": "nickelate", "temperature_c": 800},
    )
    source = kb.sources.add_text(
        collection.id,
        "custom project notes",
        filename="notes.txt",
    )
    artifact = kb.artifacts.add_bytes(
        source.id,
        b"normalized notes",
        processing_type="NORMALIZED_TEXT",
        format="txt",
    )
```

S3-compatible storage uses
`s3://bucket?endpoint=http://localhost:9000` plus the optional
`object_store_access_key` and `object_store_secret_key` arguments. Construction never
creates or migrates tables. Explicit clients deliberately reject legacy facade calls
such as `list_projects()` because those operations still depend on global application
configuration; use their grouped services as those repositories become writable.
`initialize()` creates only the portable `mkb_*` tables owned by the new SDK. It uses
additive, idempotent table creation and never drops or renames tables.
It returns the current portable schema version; `kb.schema_version()` reports the
stored version afterward. Revisions are recorded in `mkb_schema_migrations`. Revision
1 contains collections, sources, records, and schemas; revision 2 adds artifacts and
projections.

Multiple relational writes can share one commit or rollback boundary:

```python
with KnowledgeBase.from_url(database_url="sqlite:////tmp/research.db") as kb:
    kb.initialize()
    with kb.transaction() as tx:
        collection = tx.collections.create(name="Experiment 43")
        record = tx.records.create(
            collection_id=collection.id,
            data=[{"sample": "A", "result": 12.4}],
        )
        schema = tx.schemas.create(
            name="experiment-result",
            domain="my project",
            definition={"type": "array"},
            system_prompt="Extract supported experiment results.",
        )
        tx.projections.create(
            schema_id=schema.id,
            record_id=record.id,
            data={"sample": "A", "result": 12.4},
        )
```

The current transaction object includes collections, records, schemas, and projections.
Direct `sources.add_bytes(...)` and `sources.add_text(...)` calls write content first, commit
metadata second, and delete the new object if the metadata write fails. Artifact byte
registration follows the same compensation rule. Object-backed writes are not available
on the transaction object because S3 and filesystem operations cannot participate in
the relational ACID transaction.

The client already owns explicit relational and object-store resources. They are
available for health checks and are closed with the client:

```python
with KnowledgeBase.from_environment() as kb:
    kb.database.check()
    kb.object_store.check((kb.config.raw_bucket, kb.config.processed_bucket))

    # Typed generic view over the existing research_projects table.
    collections = kb.collections.list(limit=100)
    first = kb.collections.get(collections[0].id) if collections else None
    if first:
        print(first.model_dump(mode="json"))

        sources = kb.sources.list(collection_id=first.id)
        if sources:
            source = sources[0]
            with kb.sources.open(source.id) as content:
                header = content.read(16)

            artifacts = kb.artifacts.list(source_id=source.id)
            if artifacts and kb.artifacts.content_exists(artifacts[0].id):
                processed = kb.artifacts.read_bytes(artifacts[0].id)

        # Existing knowledge_frames, spaces, and projections are exposed without
        # rewriting their rows or normalizing their stored JSON payloads.
        record = kb.records.get_for_collection(first.id)
        schemas = kb.schemas.list()
        projections = kb.projections.list(
            collection_id=first.id,
            status="COMPLETED",
            newest_only=True,
        )
        json_text = kb.projections.export_json(
            collection_id=first.id,
            newest_only=True,
        )
```

The generic model mapping is deliberately compatible with the current local schema:
`research_projects` become `Collection`, `assets` become `Source`, processed assets
become `Artifact`, `knowledge_frames` become `Record`, spaces become
`ExtractionSchema`, and stored projections become `Projection`. IDs, timestamps,
status values, review metadata, schema versions, and raw JSON are retained. The
environment-backed typed services remain read-only to protect the existing local
dataset. Explicitly configured, initialized databases support collection, record, and
schema creation through portable transaction-aware repositories.

All public models support `model_dump(mode="json")` and `model_dump_json()`. The
`records.export_json(...)` and `projections.export_json(...)` helpers return JSON text
without writing files, so package consumers decide where exported data belongs.

The adapter interfaces are `mkb.Database` and `mkb.ObjectStore`; default implementations
are available from `mkb.adapters`. Existing domain services are being moved onto these
resources incrementally, so direct construction with a new database is not yet a full
replacement for `from_environment()`.

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

## Custom local pipelines

Pipeline definitions and registries belong to one `KnowledgeBase` instance. Steps run
sequentially and merge their output mappings into the accumulated state. Optional
Pydantic models validate step inputs, parameters, and outputs.

```python
from pydantic import BaseModel
from mkb import KnowledgeBase, Pipeline, Step

class Inputs(BaseModel):
    text: str

class Outputs(BaseModel):
    word_count: int

pipeline = Pipeline(
    name="count-words",
    steps=(
        Step(
            name="count",
            input_model=Inputs,
            output_model=Outputs,
            deterministic=True,
            handler=lambda context, state: {
                "word_count": len(state["text"].split()),
            },
        ),
    ),
)

with KnowledgeBase.from_url(database_url="sqlite:///:memory:") as kb:
    kb.pipelines.register(pipeline)
    run = kb.pipelines.run("count-words", inputs={"text": "custom project data"})
    print(run.model_dump(mode="json"))
```

Steps may declare `required_capabilities`, `RetryPolicy`, `cacheable`,
`timeout_seconds`, and `side_effects`. Capability requirements are checked before
execution. Cache, timeout, and side-effect fields are currently provenance
declarations; enforcement, durable jobs, cancellation, checkpointing, and caching
remain future milestones.
Failures raise `PipelineExecutionError`; its `run` attribute contains the failed typed
run and completed step history. A progress callback receives typed `ProgressEvent`
objects. Existing `Record` instances can be supplied directly in pipeline inputs, so
local extracted data does not need to be reprocessed.

Parsers and reusable standalone steps are also registered on one configured client;
they do not mutate module-global registries. Portable schemas are persisted by the
client's metadata repository:

```python
from mkb import Parser, Pipeline, Step

kb.parsers.register(
    Parser(
        name="notes",
        source_types=frozenset({"text/plain"}),
        handler=lambda context, content: {"text": content.decode("utf-8")},
    )
)
kb.steps.register(
    Step(
        name="word-count",
        deterministic=True,
        handler=lambda context, state: {"words": len(state["text"].split())},
    )
)
kb.schemas.register(
    name="notes-schema",
    domain="general",
    definition={"type": "object", "properties": {"words": {"type": "integer"}}},
    system_prompt="Extract only supported values.",
)
kb.pipelines.register(Pipeline(name="notes", steps=(kb.steps.require("word-count"),)))
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
