"""Per-call resource binding for retained legacy application services.

Legacy materials code still imports compatibility session/S3 helpers. Context-local
bindings let those imports use the owning ``KnowledgeBase`` resources without process-
global engine or object-store selection. The fallback remains for direct ``mkb.api``
compatibility calls during the deprecation window.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


database_var: ContextVar[Any | None] = ContextVar("mkb_legacy_database", default=None)
object_store_var: ContextVar[Any | None] = ContextVar(
    "mkb_legacy_object_store", default=None
)


@contextmanager
def bind_legacy_resources(database: Any | None, object_store: Any | None) -> Iterator[None]:
    """Bind compatibility persistence for the duration of one application call."""
    database_token = database_var.set(database)
    object_store_token = object_store_var.set(object_store)
    try:
        yield
    finally:
        object_store_var.reset(object_store_token)
        database_var.reset(database_token)
