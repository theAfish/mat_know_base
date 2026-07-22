"""Explicit resources and tool binding for materials agents.

ADK receives ordinary callables, while MKB agents need client-owned persistence.
``AgentRuntime`` carries those resources and ``bind_tool`` produces a callable
whose public signature excludes the injected dependencies.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

from mkb.ports import Database, ObjectStore


@dataclass(frozen=True)
class AgentRuntime:
    """Persistence owned by one agent execution/client."""

    database: Database
    object_store: ObjectStore | None = None


def bind_tool(
    operation: Callable[..., Any],
    runtime: AgentRuntime,
) -> Callable[..., Any]:
    """Bind an operation's private ``runtime`` argument for ADK registration.

    Agent tool schemas are derived from callable signatures.  The wrapper keeps
    ``runtime`` out of that schema so a model can never supply or override the
    client resources.
    """

    signature = inspect.signature(operation)
    public_parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.name != "runtime"
    ]

    @wraps(operation)
    def tool(*args: Any, **kwargs: Any) -> Any:
        return operation(*args, runtime=runtime, **kwargs)

    tool.__signature__ = signature.replace(parameters=public_parameters)  # type: ignore[attr-defined]
    return tool


def bind_tools(
    operations: list[Callable[..., Any]], runtime: AgentRuntime
) -> list[Callable[..., Any]]:
    """Bind a complete ADK tool family to one agent runtime."""

    return [bind_tool(operation, runtime) for operation in operations]
