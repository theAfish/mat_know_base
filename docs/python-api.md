# Python API

## Install and choose storage

Python 3.10+ is supported. Install `.` for the core, `.[sql]` for SQLAlchemy/SQLite,
`.[postgres]` for PostgreSQL, `.[s3]` for S3 and `.[ocr]` for Tesseract's Python
bindings. Install from a tested Git commit when consuming from another repository;
MKB is not currently published on PyPI. The base dependencies are Pydantic and
`jsonschema`; it does not load SQLAlchemy, application settings or an agent runtime.

Two constructors serve embedded applications:

- `KnowledgeBase.from_repositories(bundle, ...)`: use your own database through
  public repository contracts, with optional object/model/graph/job/vector adapters.
- `KnowledgeBase.from_url(database_url=..., object_store_url=..., ...)`: explicitly
  select the optional SQLAlchemy repositories and SQL/file/S3 object adapters.

`KnowledgeBase.from_environment()` and `mkb.api` retain the old materials
application and its global configuration. They use a different database schema.
They are compatibility APIs, not the new package integration path.

## Your own database

```python
from mkb import KnowledgeBase, RepositoryBundle

# These are your implementations of the protocols in mkb.repositories.
repositories = RepositoryBundle(
    collections=collection_repository,
    sources=source_repository,
    artifacts=artifact_repository,
    records=record_repository,
    schemas=space_repository,
    projections=projection_repository,
    evidence=evidence_repository,
)

with KnowledgeBase.from_repositories(
    repositories,
    object_store=your_object_store,
    model_provider=your_model_provider,
) as kb:
    collection = kb.collections.create(name="Research")
```

Repository operations exchange the public models from `mkb.models`, UUIDs and plain
values. They require no SQL engine or session. Implement the read protocols and
`WritableCollectionRepository`, `WritableSourceRepository`,
`WritableArtifactRepository`, `WritableRecordRepository`,
`WritableExtractionSchemaRepository`, `WritableProjectionRepository` and
`WritableEvidenceRepository` for full extraction. The signatures are defined in
[`mkb.repositories`](../src/mkb/repositories.py). A repository may wrap an existing
ORM, document collection, remote service or in-memory store.

You can omit repositories unused by your application. Read-only implementations
remain supported; unsupported writes raise `ConflictError`. Metadata operations
on sources/artifacts do not require an object store. Reading or writing bytes does.
See [custom_storage.py](../examples/custom_storage.py) for a runnable implementation
using only the base package.

`ObjectStore` has byte/stream CRUD, listing, checking and closing operations.
`FileObjectStore` works with the base package:

```python
from mkb.adapters import FileObjectStore
store = FileObjectStore("./objects")
```

The complete protocols, including `ModelProvider`, `ContentParser`, `GraphStore`,
`JobBackend` and `VectorSearch`, live in `mkb.ports`. Optional implementations in
`mkb.adapters` load on demand.

## Optional SQL convenience

```python
from pathlib import Path
from mkb import KnowledgeBase

with KnowledgeBase.from_url(
    database_url="sqlite:///knowledge.db",
    object_store_url=Path("objects").resolve().as_uri(),
) as kb:
    kb.initialize()
    collection = kb.collections.create(name="Experiment 42")
    source = kb.sources.add_text(collection.id, "Calcite is CaCO3.")
```

Install `[sql]` for SQLite; `[postgres]` for a PostgreSQL URL such as
`postgresql+psycopg://user:password@host/database`. The host application provisions
the database. Construction does not create generic repository tables;
`initialize()` creates missing `mkb_*` tables additively, and `schema_version()`
reports their version. The explicit `sql:` object-store adapter provisions its own
blob table when constructed. `file:///absolute/path` stores bytes on disk;
`s3://bucket?endpoint=https://host` selects S3. Omit `object_store_url` for
metadata-only SQL access. No connection is inferred from `.env`.

## Extraction and Space definitions

A Space combines a JSON Schema, instructions, optional preferred parser and ordered
post-processor names. `register_space` persists it through the schema repository;
`kb.schemas` provides get/list/update and immutable version history. Duplicate
names raise `ConflictError`. Projections record the schema version used.

```python
from mkb import CallableModelProvider, SpaceDefinition

# Bridge your own model/agent. The handler is synchronous and returns JSON data
# or a JSON string. `parameters` includes response_schema and space.
provider = CallableModelProvider(
    lambda prompt, *, parameters: your_agent.run(prompt, parameters=parameters),
    identity="my-agent-v1",
)
# Pass provider as model_provider when constructing kb.

space = kb.extraction.register_space(SpaceDefinition(
    name="composition",
    schema={
        "type": "object",
        "properties": {"formula": {"type": "string"}},
        "required": ["formula"],
        "additionalProperties": False,
    },
    system_prompt="Extract the chemical formula supported by the source.",
))
result = kb.extraction.extract_source(source.id, space=space.id)
print(result.data)
print(result.projection.id)
```

`extract(collection_id, space=..., source_ids=None, ...)` processes all sources in
that collection, or the supplied IDs, into one projection. `extract_source` infers
the collection only if the source belongs to exactly one; otherwise supply
`collection_id`. `space` accepts an ID or name. A collection/source must exist and
all selected sources must belong to it.

`ExtractionResult` exposes `record`, `projection`, `artifacts`, `evidence` and the
convenience property `data`. Serialize models with `model_dump(mode="json")`.
Evidence links identify the contributing documents; they are not a guarantee of
field-level scientific verification. The core does not perform autonomous web
research or scientific review.

The workflow parses inputs, calls the model, runs post-processors in declaration
order, validates the final JSON against the Space, and then persists results.
Malformed model JSON, schema violations and missing handlers fail before writes.
Schema validation resolves local references without fetching remote schemas.

## OCR and document parsers

UTF-8 text/Markdown/JSON sources work without a parser. Register a parser for PDF,
images, other encodings or your own document service:

```python
from mkb import Parser, ParsedContent

kb.parsers.register(Parser(
    name="my-ocr",
    source_types=frozenset({"application/pdf", "image/png"}),
    version="1",
    handler=lambda context, content: ParsedContent(
        text=your_ocr(content, **context.parameters),
        metadata={"engine": "my-ocr"},
    ),
))
result = kb.extraction.extract_source(source.id, space=space.id, parser="my-ocr")
```

Handlers return a string or `ParsedContent`. An explicit `parser` overrides the
Space's preference; otherwise a registered parser is selected by MIME type or
extension. Ambiguous matches require an explicit name. PDF/image input never
silently falls back to text decoding.

Two optional bridges are provided in `mkb.adapters.parsers`:

```python
from mkb.adapters.parsers import TesseractParser, ProcessorParser

# Requires [ocr] and the host's Tesseract executable/language data.
kb.parsers.register_adapter(TesseractParser(language="eng", timeout=60))

# Explicitly configured MinerU cloud client; requires [pdf], not local MinerU.
from mkb.processors.pdf_mineru_api import MinerUApiPDFProcessor
kb.parsers.register_adapter(ProcessorParser(
    name="mineru-api",
    processor=MinerUApiPDFProcessor(api_base=api_base, token=token, enable_ocr=True),
    source_types=frozenset({"application/pdf"}),
))
```

`ProcessorParser` consumes the primary text/Markdown output. Auxiliary image files
remain available through the underlying processor's native result. Local MinerU
is an opt-in `[pdf-local]` dependency; other OCR/agent frameworks can be integrated
through the same parser/provider protocols.

## Post-processing

```python
from mkb import PostProcessorDefinition, SpaceDefinition

kb.extraction.register_post_processor(PostProcessorDefinition(
    name="normalize-formula",
    version="1",
    handler=lambda context, data: {**data, "formula": data["formula"].strip()},
))
space = kb.extraction.register_space(SpaceDefinition(
    name="normalized-composition",
    schema={"type": "object", "properties": {"formula": {"type": "string"}}},
    system_prompt="Extract the chemical formula.",
    post_processors=("normalize-formula",),
))
```

A callback receives `ExtractionContext` (collection, Space, sources, parsed documents
and its parameters) and data, and returns replacement data. Pass per-callback
options using `post_processor_parameters={"normalize-formula": {...}}` when
extracting. Parser/model parameters have their own arguments. The final schema is
validated after normalization; callback versions are recorded on the projection.

Space definitions store names and parser preferences under the reserved JSON Schema
extension `x-mkb`. Callables are client-local: register them again after reopening
a client. Stored code is not imported or executed. `kb.post_processors` retains the
legacy metadata service; executable extraction callbacks belong to
`kb.extraction.post_processors`.

## Transactions, lifecycle and errors

`from_repositories` borrows resources by default. Closing the client stops its
pipeline workers but leaves supplied adapters open. With `close_resources=True`,
it closes supplied adapters and invokes the bundle's optional `close` callback.
`from_url` owns its adapters. Use a context manager or call `close()`.

A custom bundle may provide `schema_manager` with `initialize()` / `version()` and
`transaction`, a context factory yielding another bundle bound to a unit of work.
That context must commit on normal exit and roll back on exceptions. It must include
collections, records, schemas and projections, plus sources/artifacts/evidence for
extraction. The SDK compensates newly created object writes on rollback.
`kb.supports_transactions` reports availability; `kb.transaction()` exposes the
scoped repository services. Without a transaction adapter, a persistence failure
can leave partial writes; the host adapter owns recovery/retry behavior.

The synchronous API does not hide blocking I/O in async methods. Async applications
should use their worker/thread boundary. `kb.pipelines` and `kb.steps` support
custom staged workflows with progress, retries and optional job backends. See
[portable_quickstart.py](../examples/portable_quickstart.py).

Public exceptions derive from `MKBError`: `ValidationError`, `ConflictError`,
`NotFoundError`, `ProviderError`, `BackendUnavailableError` and
`PipelineExecutionError`. Original provider exceptions remain chained.

See the [generated reference](api-reference.md) for model fields and method
signatures, and [architecture](architecture-map.md) for the optional application
boundary. Existing application users can still install `[materials,server]` and use
`from_environment()`; the generic SDK neither migrates nor reinterprets that data.
