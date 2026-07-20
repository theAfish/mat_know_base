"""Small, synchronous pipeline core for embedding MKB in other Python projects."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from mkb.exceptions import (
    ConflictError,
    NotFoundError,
    PipelineExecutionError,
    ValidationError,
)

if TYPE_CHECKING:
    from mkb.sdk import KnowledgeBase

StepHandler = Callable[["StepContext", Mapping[str, Any]], Mapping[str, Any]]
ProgressHandler = Callable[["ProgressEvent"], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RetryPolicy:
    """Retry declaration for one synchronous step."""

    max_attempts: int = 1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValidationError("max_attempts must be at least 1")


@dataclass(frozen=True)
class Step:
    """One callable pipeline stage.

    A step receives the accumulated state and returns fields to merge into that state.
    """

    name: str
    handler: StepHandler
    version: str = "1"
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
    parameter_model: type[BaseModel] | None = None
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    deterministic: bool = False
    cacheable: bool = False
    side_effects: frozenset[str] = field(default_factory=frozenset)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationError("step name must not be empty")
        if self.cacheable and not self.deterministic:
            raise ValidationError("cacheable steps must be deterministic")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValidationError("timeout_seconds must be positive")


@dataclass(frozen=True)
class Pipeline:
    """Ordered collection of steps executed sequentially."""

    name: str
    steps: tuple[Step, ...]
    version: str = "1"
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationError("pipeline name must not be empty")
        if not self.steps:
            raise ValidationError("pipeline must contain at least one step")
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValidationError("step names must be unique within a pipeline")


class ProgressEvent(BaseModel):
    """Structured notification emitted during a synchronous pipeline run."""

    model_config = ConfigDict(frozen=True)

    run_id: uuid.UUID
    event: str
    step_name: str | None = None
    attempt: int | None = None
    message: str | None = None
    occurred_at: datetime = Field(default_factory=_now)


class StepRun(BaseModel):
    """Serializable execution record for one step."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    version: str
    status: str
    attempts: int
    deterministic: bool = False
    cacheable: bool = False
    required_capabilities: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    timeout_seconds: float | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime
    completed_at: datetime


class PipelineRun(BaseModel):
    """Serializable result and provenance for one local pipeline execution."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    pipeline_name: str
    pipeline_version: str
    status: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    steps: tuple[StepRun, ...] = ()
    error: str | None = None
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True)
class StepContext:
    """Resources and immutable run metadata supplied to step handlers."""

    knowledge_base: KnowledgeBase
    run_id: uuid.UUID
    pipeline_name: str
    step_name: str
    parameters: Mapping[str, Any]
    attempt: int


class Pipelines:
    """Per-client pipeline registry and synchronous executor."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        *,
        capabilities: frozenset[str] = frozenset(),
    ):
        self._knowledge_base = knowledge_base
        self._capabilities = capabilities
        self._registry: dict[str, Pipeline] = {}
        self._runs: dict[uuid.UUID, PipelineRun] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    def register(self, pipeline: Pipeline, *, replace: bool = False) -> Pipeline:
        if pipeline.name in self._registry and not replace:
            raise ConflictError(f"Pipeline already registered: {pipeline.name}")
        self._registry[pipeline.name] = pipeline
        return pipeline

    def get(self, name: str) -> Pipeline | None:
        return self._registry.get(name)

    def require(self, name: str) -> Pipeline:
        pipeline = self.get(name)
        if pipeline is None:
            raise NotFoundError(f"Pipeline not found: {name}")
        return pipeline

    def list(self) -> list[Pipeline]:
        return [self._registry[name] for name in sorted(self._registry)]

    def get_run(self, run_id: str | uuid.UUID) -> PipelineRun | None:
        try:
            identifier = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("run_id must be a UUID") from exc
        return self._runs.get(identifier)

    def run(
        self,
        pipeline: Pipeline | str,
        *,
        inputs: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Any] | None = None,
        progress: ProgressHandler | None = None,
    ) -> PipelineRun:
        definition = self.require(pipeline) if isinstance(pipeline, str) else pipeline
        missing = set().union(
            *(step.required_capabilities for step in definition.steps)
        ) - self._capabilities
        if missing:
            names = ", ".join(sorted(missing))
            raise PipelineExecutionError(f"Missing pipeline capabilities: {names}")

        run_id = uuid.uuid4()
        started_at = _now()
        original_inputs = dict(inputs or {})
        run_parameters = dict(parameters or {})
        state = dict(original_inputs)
        step_runs: list[StepRun] = []
        self._emit(progress, run_id, "pipeline_started")

        try:
            for step in definition.steps:
                step_run = self._run_step(
                    definition,
                    step,
                    run_id,
                    state,
                    run_parameters,
                    progress,
                )
                step_runs.append(step_run)
                state.update(step_run.output)
        except Exception as exc:
            failed_step = getattr(exc, "step_run", None)
            if isinstance(failed_step, StepRun):
                step_runs.append(failed_step)
            completed_at = _now()
            result = PipelineRun(
                id=run_id,
                pipeline_name=definition.name,
                pipeline_version=definition.version,
                status="FAILED",
                inputs=original_inputs,
                parameters=run_parameters,
                outputs=state,
                steps=tuple(step_runs),
                error=str(exc),
                started_at=started_at,
                completed_at=completed_at,
            )
            self._runs[run_id] = result
            self._emit(progress, run_id, "pipeline_failed", message=str(exc))
            if isinstance(exc, PipelineExecutionError):
                exc.run = result
                raise
            raise PipelineExecutionError(str(exc), run=result) from exc

        result = PipelineRun(
            id=run_id,
            pipeline_name=definition.name,
            pipeline_version=definition.version,
            status="COMPLETED",
            inputs=original_inputs,
            parameters=run_parameters,
            outputs=state,
            steps=tuple(step_runs),
            started_at=started_at,
            completed_at=_now(),
        )
        self._runs[run_id] = result
        self._emit(progress, run_id, "pipeline_completed")
        return result

    def _run_step(
        self,
        pipeline: Pipeline,
        step: Step,
        run_id: uuid.UUID,
        state: Mapping[str, Any],
        parameters: Mapping[str, Any],
        progress: ProgressHandler | None,
    ) -> StepRun:
        started_at = _now()
        last_error: Exception | None = None
        for attempt in range(1, step.retry.max_attempts + 1):
            self._emit(progress, run_id, "step_started", step.name, attempt)
            step_state = (
                dict(step.input_model.model_validate(dict(state)))
                if step.input_model is not None
                else state
            )
            step_parameters = (
                dict(step.parameter_model.model_validate(dict(parameters)))
                if step.parameter_model is not None
                else parameters
            )
            context = StepContext(
                knowledge_base=self._knowledge_base,
                run_id=run_id,
                pipeline_name=pipeline.name,
                step_name=step.name,
                parameters=step_parameters,
                attempt=attempt,
            )
            try:
                output = step.handler(context, step_state)
                if not isinstance(output, Mapping):
                    raise TypeError("step output must be a mapping")
                output_data = dict(output)
                if step.output_model is not None:
                    output_data = step.output_model.model_validate(output_data).model_dump()
                result = StepRun(
                    name=step.name,
                    version=step.version,
                    status="COMPLETED",
                    attempts=attempt,
                    deterministic=step.deterministic,
                    cacheable=step.cacheable,
                    required_capabilities=tuple(sorted(step.required_capabilities)),
                    side_effects=tuple(sorted(step.side_effects)),
                    timeout_seconds=step.timeout_seconds,
                    output=output_data,
                    started_at=started_at,
                    completed_at=_now(),
                )
                self._emit(progress, run_id, "step_completed", step.name, attempt)
                return result
            except Exception as exc:
                last_error = exc
                self._emit(
                    progress,
                    run_id,
                    "step_attempt_failed",
                    step.name,
                    attempt,
                    str(exc),
                )

        message = f"Step {step.name!r} failed after {step.retry.max_attempts} attempt(s)"
        step_run = StepRun(
            name=step.name,
            version=step.version,
            status="FAILED",
            attempts=step.retry.max_attempts,
            deterministic=step.deterministic,
            cacheable=step.cacheable,
            required_capabilities=tuple(sorted(step.required_capabilities)),
            side_effects=tuple(sorted(step.side_effects)),
            timeout_seconds=step.timeout_seconds,
            error=str(last_error),
            started_at=started_at,
            completed_at=_now(),
        )
        raise PipelineExecutionError(f"{message}: {last_error}", step_run=step_run) from last_error

    @staticmethod
    def _emit(
        progress: ProgressHandler | None,
        run_id: uuid.UUID,
        event: str,
        step_name: str | None = None,
        attempt: int | None = None,
        message: str | None = None,
    ) -> None:
        if progress is not None:
            progress(
                ProgressEvent(
                    run_id=run_id,
                    event=event,
                    step_name=step_name,
                    attempt=attempt,
                    message=message,
                )
            )
