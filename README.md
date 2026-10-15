# mat-know-base

MKB is embeddable Python infrastructure for storing source material and turning it
into structured knowledge. Its core has two parts:

- **Storage contracts** for collections, sources, parsed artifacts, records, spaces,
  projections and evidence. Supply repositories backed by your existing database.
- **Extraction workflows** for parsing/OCR, agent projection against a versioned
  Space, schema validation and ordered Python post-processors.

The base package depends on Pydantic and JSON Schema validation. It does not select
a database, read application settings, start services or require Docker, Node.js,
SQLAlchemy or an agent framework. SQL storage and the previous materials/Web
application are optional integrations.

## Install

Python 3.10+ is required. From a checkout:

```bash
python -m pip install .          # core; bring your own repositories and model
python -m pip install '.[sql]'   # optional SQLite / SQLAlchemy adapter
```

The package is not published on PyPI. For another project, install from a tested
commit of this repository:

```bash
python -m pip install \
  'mat-know-base[sql] @ git+https://github.com/theAfish/mat_know_base.git@<commit>'
```

Extras include `postgres`, `s3`, `neo4j`, `ocr`, `pdf`, and `pdf-local`.
`agents` enables the legacy ADK integration; `materials,server` enables the previous
application. Local MinerU is isolated in `pdf-local` (also included by the full
application extras). The core accepts your own OCR and agent callables.

## Python API

Use `KnowledgeBase.from_repositories(RepositoryBundle(...), ...)` to connect your
own storage. Repository protocols are in `mkb.repositories`; object storage and
model protocols are in `mkb.ports`. No ORM/session interface is required.

For a runnable example, explicitly choose SQLite and a filesystem object store:

```python
from pathlib import Path
from mkb import CallableModelProvider, KnowledgeBase, SpaceDefinition

# Replace this demo handler with your model or agent call.
provider = CallableModelProvider(
    lambda prompt, *, parameters: {"formula": "CaCO3"},
    identity="demo",
)

with KnowledgeBase.from_url(
    database_url="sqlite:///knowledge.db",
    object_store_url=Path("objects").resolve().as_uri(),
    model_provider=provider,
) as kb:
    kb.initialize()  # explicit, additive SQL schema creation
    collection = kb.collections.create(name="Paper")
    source = kb.sources.add_text(collection.id, "Calcite is CaCO3.")
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
```

This persists parsed text, a source record, a validated projection and evidence
links. Register OCR with `kb.parsers.register(...)` or `register_adapter(...)`;
register transformations with `kb.extraction.register_post_processor(...)` and
reference their names in `SpaceDefinition.post_processors`.

See [Python API](docs/python-api.md) for custom storage, OCR, models, post-processing,
transactions and lifecycle. Runnable examples:

```bash
python examples/custom_storage.py        # base package; no SQL
python examples/extraction_quickstart.py # requires [sql]; no network/model account
python examples/portable_quickstart.py   # requires [sql]; generic pipelines
```

## Development

```bash
make bootstrap  # package development dependencies only
make check      # package lint and portable tests
make build      # Python wheel
```

The existing React application, HTTP server, CLI application commands and historical
workflows remain compatibility features. They are excluded from the default
development workflow. `make install-app`, `make dev`, and `make test-app` opt into
them; provision their PostgreSQL server externally. No bundled container lifecycle
is required or provided. Existing data is not migrated by this change.

Start with the [documentation index](docs/README.md),
[architecture](docs/architecture-map.md) and [development guide](docs/development.md).
