"""Materials compatibility operations exposed as built-in pipeline definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mkb.pipelines import Pipeline, Step, StepContext


@dataclass(frozen=True)
class _Operation:
    pipeline_name: str
    service_name: str
    progress: bool = False


_OPERATIONS = (
    _Operation("materials.ingest", "ingest"),
    _Operation("materials.process", "process", progress=True),
    _Operation("materials.extract_frames", "extract", progress=True),
    _Operation("materials.project", "project", progress=True),
    _Operation("materials.extract_graph", "extract_knowledge_graph", progress=True),
    _Operation("materials.extract_workflow", "extract_raw_workflow", progress=True),
    _Operation("materials.review_schema", "review_schema_proposal"),
    _Operation("materials.review_feedback", "review_feedback", progress=True),
)


def _progress(context: StepContext, event: Any) -> None:
    if isinstance(event, Mapping):
        payload = dict(event)
        message = str(payload.pop("message", payload.pop("label", "Working")))
        context.emit(message, **payload)
    else:
        context.emit(str(event))


def _handler(operation: _Operation):
    def invoke(context: StepContext, state: Mapping[str, Any]) -> Mapping[str, Any]:
        kwargs = dict(state)
        kwargs.update(context.parameters)
        if operation.progress:
            kwargs["progress_callback"] = lambda event: _progress(context, event)
        result = context.knowledge_base.service(operation.service_name)(**kwargs)
        return {"result": result}

    return invoke


def materials_builtin_pipelines() -> tuple[Pipeline, ...]:
    """Return fresh definitions that delegate to existing operations unchanged."""
    return tuple(
        Pipeline(
            name=operation.pipeline_name,
            description=f"Built-in compatibility pipeline for {operation.service_name}",
            steps=(
                Step(
                    name=operation.service_name,
                    handler=_handler(operation),
                    side_effects=frozenset({"legacy_materials_application"}),
                ),
            ),
        )
        for operation in _OPERATIONS
    )


def register_materials_builtin_pipelines(knowledge_base) -> tuple[Pipeline, ...]:
    """Register all built-ins on one client without using a global registry."""
    definitions = materials_builtin_pipelines()
    for pipeline in definitions:
        knowledge_base.pipelines.register(pipeline)
    return definitions
