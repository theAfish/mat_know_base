# Architecture and ownership

MKB's reusable core consists of storage contracts and an extraction workflow.
Applications supply their infrastructure and own deployment.

```text
Host Python application
        │
        ▼
KnowledgeBase.from_repositories(RepositoryBundle, ObjectStore, ModelProvider)
        │
        ├── collections / sources / artifacts / schemas / records / projections / evidence
        │       └── repository protocols → host database or optional SQL adapters
        │
        └── extraction
                source bytes → parser / OCR → agent projection
                  → ordered post-processors → JSON Schema validation → persistence
```

## Core ownership

| Module | Responsibility |
| --- | --- |
| `mkb.models` | Serializable public data models |
| `mkb.repositories` | Read/write repository protocols and validated operations |
| `mkb.storage` | Repository composition, optional schema and transaction lifecycle |
| `mkb.ports` | Object storage, model, parser, graph, jobs and vector capabilities |
| `mkb.sdk` | Client composition and resource ownership |
| `mkb.extraction` | Space definitions, parsing/projection workflow and callable post-processing |
| `mkb.registries`, `mkb.pipelines` | Per-client parser and reusable step registration/execution |
| `mkb.adapters` | Optional integrations, loaded on request |

Core modules do not read `.env`, import database models, instantiate agent runtimes
or provision infrastructure. Repositories exchange public models and UUIDs. The
`Database` session interface is an implementation detail of SQL adapters; custom
storage integrates at the repository boundary, including document/remote stores.
`from_url` explicitly selects SQLAlchemy. It is a convenience adapter factory, not
a default storage choice.

Parsed source text and provider outputs are processed before persistence. SQL
writes use one transaction; new object writes register rollback compensation.
A custom bundle can supply a transaction context with equivalent semantics. Without
it, persistence can leave partial writes on failure. Providers and parsers execute
outside the database transaction.

## Optional compatibility application

`mkb.api`, `KnowledgeBase.from_environment`, `mkb.materials`, `mkb.services`,
`mkb.agents`, `mkb.web`, legacy CLI commands, `mkb.db`, maintenance scripts and
`frontend/` retain the prior materials application. Its PostgreSQL schema differs
from the generic `mkb_*` schema. Use its own configuration and lifecycle; do not
point generic repositories at legacy tables or silently migrate existing data.
The frontend and deployment tools are not required to install or use the package.

The previous application remains available for existing consumers. New embeddings
should use `kb.extraction` and repository interfaces. Graph review, UI assistant,
canonical workflow management and uploaded executable scripts are not part of the
core extraction contract.

Review routing remains in `CODEOWNERS`. Public API changes need documentation and
contract coverage; persistence changes need adapter and transaction coverage.
