from contextvars import ContextVar

CURATOR_AUTHOR: ContextVar[str] = ContextVar(
    'schema_curator_author', default='schema-curator-agent/unknown-model',
)
