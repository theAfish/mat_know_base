# Python API

## Installation and supported surfaces

MKB is currently distributed from this repository rather than PyPI. Install the base
package for the portable typed SDK, SQLite, filesystem storage, registries, and local
pipeline execution from a pinned commit for reproducible consumers (replace
`<commit>` with the version your repository has tested):

```bash
python -m pip install \
  "mat-know-base @ git+https://github.com/theAfish/mat_know_base.git@<commit>"
```

Use the `dev` branch only when intentionally tracking the shared development build.
Install extras only for the integrations a consumer uses. For example, PostgreSQL and
S3 support use:

```bash
python -m pip install \
  "mat-know-base[postgres,s3] @ git+https://github.com/theAfish/mat_know_base.git@<commit>"
```

Other extras are `[neo4j]`, `[materials]`, and `[server]`. The existing materials
application, agent-backed extraction, and its compatibility facade require
`[materials]`; its HTTP server additionally requires `[server]`. Once MKB is
published on PyPI, these Git URLs can be replaced with normal package-install commands.

There are two supported Python surfaces. `KnowledgeBase.from_url(...)` is the portable,
typed SDK intended for new repositories. `KnowledgeBase.from_environment()` and
`mkb.api` are the materials application's compatibility surfaces: they require its
configured infrastructure and may return legacy dictionaries. Do not mix identifiers or
assume that a portable client can operate on the legacy application schema.

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
bindings belong to one object rather than module globals. The environment adapter keeps
the compatibility application services, but binds their database and object-store access
to the owning client for each call. Explicit SQLite, PostgreSQL, filesystem, S3, and graph
adapters are independently configurable and do not share client registries or workers.

The supported client is intentionally synchronous. The current SQLAlchemy repositories,
S3/filesystem adapters, model-provider boundary, and graph adapters expose synchronous
operations; wrapping them in `async def` would still block an event loop. Durable work
uses `kb.pipelines.submit(...)`/`kb.jobs`, and asynchronous applications should call the
synchronous SDK at their worker/thread boundary. An async client will be added only when
the injected ports have genuinely asynchronous implementations.

## Client construction and lifecycle

New integrations should use `KnowledgeBase.from_url(...)`. It is the supported portable
composition path and accepts SQLite or SQLAlchemy PostgreSQL URLs plus built-in
filesystem or S3 object-store URLs. `from_environment()` is only for the configured
materials application. `from_url()` never reads application settings and never creates
tables; call `initialize()` deliberately for a new portable database.

```python
from mkb import KnowledgeBase

with KnowledgeBase.from_url(
    database_url="sqlite:////absolute/path/project.db",
    object_store_url="file:///absolute/path/objects",  # omit for metadata-only use
) as kb:
    kb.initialize()
    # use kb.collections, kb.records, kb.schemas, and other grouped services
```

The context manager owns and closes the constructed database, storage, graph, model,
job, and vector resources. Do not use the client after leaving the block. If an
application must create it outside a `with` block, call `kb.close()` after all submitted
pipeline jobs have finished or been cancelled.

`from_url()` accepts injected `GraphStore`, `ModelProvider`, `JobBackend`, and
`VectorSearch` implementations. Custom `Database` and `ObjectStore` implementations
are **not yet a supported public composition path**: `from_url()` constructs MKB's
built-in SQLAlchemy and filesystem/S3 adapters, while direct `KnowledgeBase(...)`
construction requires internal repository/schema bindings. Adapter authors can rely on
the protocols in `mkb.ports`, but should not depend on private SDK builders; request or
contribute a public adapter factory before using a non-built-in database or object store
in another repository.

For pipeline execution and typed repository access without reading `.env` or YAML,
construct an independent client explicitly:

```python
from mkb import KnowledgeBase

with KnowledgeBase.from_url(
    database_url="sqlite:////absolute/path/project.db",
    object_store_url="file:///absolute/path/objects",
    raw_bucket="inputs",
    processed_bucket="derived",
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
`object_store_access_key` and `object_store_secret_key` arguments. The S3 URL's bucket
is the raw/input bucket; configure `processed_bucket`, `archive_bucket`, and
`temp_bucket` explicitly when their names differ from the defaults. Filesystem storage
uses all four bucket names as directories under its root. Construction never creates or
migrates tables. Explicit clients deliberately reject legacy facade calls
such as `list_projects()` because those operations still depend on global application
configuration; use their grouped services as those repositories become writable.
`initialize()` creates only the portable `mkb_*` tables owned by the new SDK. It uses
additive, idempotent table creation and never drops or renames tables.
It returns the current portable schema version; `kb.schema_version()` reports the
stored version afterward. Revisions are recorded in `mkb_schema_migrations`. Revision
1 contains collections, sources, records, and schemas; revision 2 adds artifacts and
projections; revision 3 adds generic evidence links; revision 4 adds durable local
pipeline jobs; revision 5 adds portable feedback, skills, and post-processor metadata;
revision 6 adds portable collection groups and memberships; revision 7 adds immutable
extraction-schema revisions; revision 8 gives external source URIs dedicated storage so
URI state cannot collide with consumer metadata. Schema creation and every update write
a complete snapshot in the same relational transaction as the current schema row. This
lets a stored projection resolve the exact definition that produced it:

```python
projection = kb.projections.require(projection_id)
schema_at_extraction = kb.schemas.require_version(
    projection.schema_id,
    projection.schema_version,
)

# Revisions are returned newest first.
schema_history = kb.schemas.history(projection.schema_id)
```

When upgrading an older portable database, revision 7 snapshots each schema definition
that is current at migration time. Definitions overwritten before revision 7 did not
exist independently and therefore cannot be reconstructed by the migration.

## PostgreSQL and MinIO/S3

An explicitly configured PostgreSQL/S3 client owns its adapters and does not read global
settings:

```python
from mkb import KnowledgeBase

with KnowledgeBase.from_url(
    database_url="postgresql+psycopg://mkb:password@localhost:5432/mkb",
    object_store_url="s3://raw?endpoint=http://localhost:9000",
    object_store_access_key="...",
    object_store_secret_key="...",
) as kb:
    kb.database.check()
    kb.object_store.check((kb.config.raw_bucket, kb.config.processed_bucket))
    # Explicit and additive; omit this call for a read-only validation connection.
    kb.initialize()
```

Use `KnowledgeBase.from_environment()` for the existing materials deployment. It maps
legacy projects, groups, assets, frames, spaces, projections, feedback, skills,
post-processors, and graph operations through injected adapters without copying IDs or
object keys.

## Optional Neo4j graph storage

Install `mat-know-base[neo4j]` and inject the optional adapter into an explicitly
configured client. Importing the base package does not import or require the Neo4j
driver.

```python
from mkb import KnowledgeBase
from mkb.adapters import Neo4jGraphStore

graph = Neo4jGraphStore(
    "neo4j://localhost:7687",
    auth=("neo4j", "password"),
    database="neo4j",
)
with KnowledgeBase.from_url(
    database_url="sqlite:////absolute/path/project.db",
    object_store_url="file:///absolute/path/objects",
    graph_store=graph,
) as kb:
    entity = kb.graph.upsert_entity(type="material", name="Calcite")
```

The adapter stores backend-neutral entity/relation IDs and JSON properties beneath
fixed `MKBEntity`/`MKBRelation` types, so user-provided values are parameters rather
than Cypher identifiers.

Without an injected `graph_store`, portable clients use an `InMemoryGraphStore`. It is
isolated to that client and emptied when the client closes; SQLite does not persist graph
entities or relations. Inject Neo4j or another `GraphStore` implementation whenever a
consumer needs graph data after process restart. The in-memory default is appropriate for
tests and short-lived local pipelines only.

## Safe migration/read-validation example

Opening a client never runs migrations. A preservation-first validation can therefore
inspect existing data without writing:

```python
from mkb import KnowledgeBase

with KnowledgeBase.from_environment() as kb:
    inventory = kb.maintenance.inventory()
    reconciliation = kb.maintenance.reconcile()
    assert reconciliation.ok, reconciliation.model_dump(mode="json")

    for collection in kb.collections.list(limit=1000):
        assert kb.collections.get(collection.id) == collection
        for source in kb.sources.list(collection_id=collection.id, limit=1000):
            if source.storage is not None:
                assert kb.sources.content_exists(source.id)
```

Run additive initialization/backfills separately only after the pre-migration snapshot
and restore drill required by `TODO.md`. Never use initialization as an implicit startup
side effect, and compare inventories/reconciliation before switching readers.

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

The transaction object includes collections, sources, artifacts, records, schemas,
projections, and evidence. Object-backed writes place content first, commit metadata
second, and register reverse-order best-effort cleanup if the relational transaction
rolls back. S3/filesystem writes are compensating operations, not part of relational
ACID atomicity.

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

Legacy raw workflow extraction versions are available without graph normalization via
`kb.materials.workflows.get(...)` and `.list(...)`. The resulting `WorkflowRecord`
preserves workflow/schema versions, correction and review fields, graph, checkpoint,
provenance, errors, and timestamps. Portable clients can persist provenance with
`kb.evidence.create(...)` and query it by output, source, or artifact ID.

Portable resource lifecycle operations include collection update/safe delete, managed
file/bytes/text ingestion, recursive directory ingestion, external URI registration
without copying, structured JSON-record batches, and registration of an object already
produced by an external processor. External URI sources deliberately reject
`open()`/`read_bytes()` because their content is not owned by the configured store.

Records support exact top-level JSON field queries through `kb.records.query(...)` and
their provenance through `kb.records.evidence(record_id)`. Portable extraction schemas
increment `version` on update and refuse deletion while projections reference them.
Feedback, skills, and post-processors are typed, client-owned services:

```python
feedback = kb.feedback.create(
    target_record_id=record.id,
    target_collection_id=collection.id,
    category="ambiguous_data",
    question="Which unit applies?",
)
kb.feedback.review(feedback.id)
kb.feedback.resolve(feedback.id, notes="The source specifies kelvin.")

skill = kb.skills.create(
    name="Normalize units",
    content="# Normalize units\nConvert reported measurements to SI.",
)
processor = kb.post_processors.register(
    name="Choose projection",
    source="print('{}')\n",
)
```

Registration stores post-processor source but does not execute it; execution remains
behind the existing administrator opt-in and sandbox policy.

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

Narrow adapter protocols live in `mkb.ports`: relational database, object storage,
graph storage, vector search, content parser, model provider, and job backend. Default
database, S3/MinIO, filesystem, and in-memory graph implementations are available from
`mkb.adapters`. Today the public client factory composes the built-in database and
object-store adapters; graph, model-provider, job, and vector adapters can be injected
into `from_url(...)`. These ports remain separate: there is no artificial storage
interface spanning relational transactions, blobs, vectors, and graph traversal.

Adapters declare stable capability names through `Capabilities`. Pipeline steps fail
before execution when requirements such as `vector_search`, `full_text_search`,
`object_streaming`, or `graph_traversal` are unavailable. Adapter conformance tests
cover lifecycle, transactions, streaming, CRUD semantics, structural repository
contracts, and capability composition.

The materials compatibility API performs real database, object-storage, filesystem,
processor, and LLM work. Configure `.env`, start infrastructure with `make up`, and run
those calls from the repository root so application configuration and local data paths
resolve. The portable SDK needs only the adapters supplied to `from_url(...)`; its
default graph is the documented in-memory exception.

## End-to-end lifecycle

```python
from pathlib import Path
from mkb import api

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
in stable dependency order and merge their output mappings into the accumulated state;
independent steps retain declaration order. Optional Pydantic models validate step
inputs, parameters, and outputs.

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
execution. `depends_on` declares DAG edges, and cycles or unknown dependencies are
rejected at definition time. A timeout stops the pipeline from waiting, marks the
attempt failed, and signals `context.cancelled`; long-running handlers, especially
those with side effects, should call `context.check_cancelled()` at safe boundaries so
their worker can unwind promptly.
Failures raise `PipelineExecutionError`; its `run` attribute contains the failed typed
run and completed step history. A progress callback receives typed `ProgressEvent`
objects. Existing `Record` instances can be supplied directly in pipeline inputs, so
local extracted data does not need to be reprocessed.

Portable clients support persisted submission using the same definition. Initialize
the client first so the additive jobs table is available:

```python
kb.pipelines.register(pipeline)
job = kb.pipelines.submit(
    "count-words",
    inputs={"text": "custom project data"},
    idempotency_key="count:source-42:v1",
)
completed = kb.jobs.wait(job.id, timeout=60)
```

Jobs retain inputs, parameters, stable run IDs, completed-step checkpoints, structured
progress/log events, attempts, results, and errors. The built-in `submit()` executor is a
daemon thread in the process that owns the client: persistence makes its state inspectable
and resumable, but it is not an external queue or a cross-process worker. On restart,
register the same pipeline version, call `kb.jobs.recover_interrupted()`, then call
`kb.pipelines.resume(job.id)`; completed steps in a compatible checkpoint are not
repeated. `kb.jobs.cancel(...)` requests cooperative cancellation at a step/event
boundary. Reusing an idempotency key returns the original job. Custom workers may enqueue
non-pipeline work with `kb.jobs.submit(...)` and must implement their own claim/execute
loop through a `JobBackend`. Persisted events can be consumed as a snapshot with
`kb.jobs.events(job.id)` or followed until a terminal state with
`kb.jobs.events(job.id, follow=True)`.

Cacheable steps must be deterministic and provide a `cache_key` builder returning
`CacheKeyComponents`. The components require configuration, source fingerprint, model
identity, and schema version; MKB additionally includes the step name and version before
hashing. A cache hit is recorded on `StepRun` and does not invoke the handler.

Environment-backed clients register built-in materials pipelines named
`materials.ingest`, `materials.process`, `materials.extract_frames`,
`materials.project`, `materials.extract_graph`, `materials.extract_workflow`,
`materials.review_schema`, and `materials.review_feedback`. Their single steps delegate
to the existing compatibility operations and retain the original result under the
pipeline output's `result` key.

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

Most mutating compatibility functions return a summary dictionary containing stable
identifiers and counts. Typed SDK reads return a public model, a list of models, or
`None` when a singular resource does not exist. Invalid IDs, missing prerequisites,
storage errors, and provider failures raise exceptions; library callers should catch
`MKBError` at a job or request boundary rather than infer success from partial output.
Use `ValidationError` for invalid caller input, `NotFoundError` for required resources,
`ConflictError` for state/precondition failures, `BackendUnavailableError` for missing or
closed integrations, `ProviderError` for external provider failures, and
`PipelineExecutionError` for a failed pipeline (whose `run` contains the typed result).

## Setup, ingest, and assets

| Call | Purpose and important arguments |
| --- | --- |
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

## Adapter and lifecycle guidance

Custom `GraphStore`, `ModelProvider`, `JobBackend`, and `VectorSearch` adapters can be
passed to `from_url(...)`. Implement the corresponding narrow protocol in `mkb.ports`
and declare only the stable `Capabilities` the adapter truly supports; pipeline steps
validate requirements before execution. The client owns injected adapters and closes
them with `kb.close()` (or a `with` block), so do not share an adapter instance across
clients unless it explicitly supports that lifecycle. Custom `Database` and
`ObjectStore` adapters remain an extension point, not a public client-construction
workflow; see [Client construction and lifecycle](#client-construction-and-lifecycle).

`KnowledgeBase` is synchronous. Use it at a worker/thread boundary from async
applications, keep SQLAlchemy sessions within that boundary, and use `kb.transaction()`
when grouped relational changes must commit or roll back together. Object-store writes
use compensating cleanup and are not part of relational ACID transactions.
