"""Small, synchronous pipeline core for embedding MKB in other Python projects."""

from __future__ import annotations

import json
import hashlib
import threading
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
from mkb.models import Job
from mkb.ports import JobBackend

if TYPE_CHECKING:
    from mkb.sdk import KnowledgeBase

StepHandler = Callable[["StepContext", Mapping[str, Any]], Mapping[str, Any]]
ProgressHandler = Callable[["ProgressEvent"], None]
CacheKeyBuilder = Callable[["StepContext", Mapping[str, Any]], "CacheKeyComponents"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_safe(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            default=lambda item: (
                item.model_dump(mode="json")
                if isinstance(item, BaseModel)
                else str(item)
            ),
        )
    )


class _PipelineCancelled(Exception):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    """Retry declaration for one synchronous step."""

    max_attempts: int = 1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValidationError("max_attempts must be at least 1")


class CacheKeyComponents(BaseModel):
    """Required identity inputs for deterministic step caching."""

    model_config = ConfigDict(frozen=True)

    configuration: dict[str, Any] = Field(default_factory=dict)
    source_fingerprint: str
    model_identity: str
    schema_version: str


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
    cache_key: CacheKeyBuilder | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationError("step name must not be empty")
        if self.cacheable and not self.deterministic:
            raise ValidationError("cacheable steps must be deterministic")
        if self.cacheable and self.cache_key is None:
            raise ValidationError("cacheable steps require a cache_key builder")
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
    level: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
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
    cached: bool = False
    cache_key: str | None = None
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
    _event_sink: Callable[[str, str, str | None, dict[str, Any]], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def emit(self, message: str, **payload: Any) -> None:
        """Emit structured step progress to local callbacks and durable jobs."""
        if self._event_sink is not None:
            self._event_sink("step_progress", message, None, payload)

    def log(self, message: str, *, level: str = "INFO", **fields: Any) -> None:
        """Emit a structured per-step log event."""
        if self._event_sink is not None:
            self._event_sink("step_log", message, level.upper(), fields)


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
        self._job_backend: JobBackend | None = None
        self._workers: dict[uuid.UUID, threading.Thread] = {}
        self._worker_lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = threading.Lock()

    def _bind_job_backend(self, backend: JobBackend | None) -> None:
        self._job_backend = backend

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
        _run_id: uuid.UUID | None = None,
        _resume_checkpoint: Mapping[str, Any] | None = None,
        _checkpoint: Callable[[dict[str, Any], tuple[StepRun, ...]], None] | None = None,
    ) -> PipelineRun:
        definition = self.require(pipeline) if isinstance(pipeline, str) else pipeline
        missing = set().union(
            *(step.required_capabilities for step in definition.steps)
        ) - self._capabilities
        if missing:
            names = ", ".join(sorted(missing))
            raise PipelineExecutionError(f"Missing pipeline capabilities: {names}")

        run_id = _run_id or uuid.uuid4()
        started_at = _now()
        original_inputs = dict(inputs or {})
        run_parameters = dict(parameters or {})
        checkpoint_data = dict(_resume_checkpoint or {})
        state = dict(checkpoint_data.get("state") or original_inputs)
        step_runs = [
            StepRun.model_validate(item) for item in checkpoint_data.get("steps", ())
        ]
        completed_names = [item.name for item in step_runs]
        expected_names = [step.name for step in definition.steps[: len(step_runs)]]
        if completed_names != expected_names:
            raise PipelineExecutionError("Pipeline checkpoint does not match definition")
        self._emit(progress, run_id, "pipeline_started")

        try:
            for step in definition.steps[len(step_runs) :]:
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
                if _checkpoint is not None:
                    _checkpoint(dict(state), tuple(step_runs))
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

    def submit(
        self,
        pipeline: Pipeline | str,
        *,
        inputs: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        """Persist and asynchronously execute one registered pipeline definition."""
        if self._job_backend is None:
            raise ConflictError("Durable jobs are unavailable for this client")
        definition = self.require(pipeline) if isinstance(pipeline, str) else pipeline
        if self.get(definition.name) is None:
            self.register(definition)
        elif self.get(definition.name) is not definition and not isinstance(pipeline, str):
            raise ConflictError(f"Pipeline already registered: {definition.name}")
        now = _now()
        job = Job(
            id=uuid.uuid4(),
            kind="pipeline",
            status="QUEUED",
            label=definition.name,
            pipeline_name=definition.name,
            pipeline_version=definition.version,
            run_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
            inputs=_json_safe(dict(inputs or {})),
            parameters=_json_safe(dict(parameters or {})),
            message="Queued",
            created_at=now,
        )
        persisted = self._job_backend.create(job)
        if persisted.id != job.id:
            return persisted
        self._start_job(persisted)
        return persisted

    def resume(self, job_id: str | uuid.UUID) -> Job:
        """Resume a failed, cancelled, or interrupted job from its last checkpoint."""
        if self._job_backend is None:
            raise ConflictError("Durable jobs are unavailable for this client")
        job = self._job_backend.get(str(job_id))
        if job is None:
            raise NotFoundError(f"Job not found: {job_id}")
        if job.status not in {"FAILED", "CANCELLED", "INTERRUPTED"}:
            raise ConflictError(f"Job cannot be resumed from status {job.status}")
        resumed = self._job_backend.update(
            str(job.id),
            status="QUEUED",
            message="Queued for resumption",
            error=None,
            completed_at=None,
        )
        self._start_job(resumed)
        return resumed

    def _start_job(self, job: Job) -> None:
        thread = threading.Thread(target=self._execute_job, args=(job.id,), daemon=True)
        with self._worker_lock:
            self._workers[job.id] = thread
        thread.start()

    def _execute_job(self, job_id: uuid.UUID) -> None:
        backend = self._job_backend
        if backend is None:
            return
        job = backend.get(str(job_id))
        if job is None or job.status == "CANCELLED":
            return
        definition = self.require(job.pipeline_name or "")
        job = backend.update(
            str(job.id),
            status="RUNNING",
            message="Running",
            started_at=_now(),
            attempt_count=job.attempt_count + 1,
        )

        def persist_event(event: ProgressEvent) -> None:
            current = backend.get(str(job.id))
            if current is None:
                raise _PipelineCancelled()
            if current.status in {"CANCELLING", "CANCELLED"}:
                raise _PipelineCancelled()
            events = (*current.events, event.model_dump(mode="json"))
            progress = current.progress
            if event.event == "step_completed":
                completed = sum(1 for item in events if item.get("event") == "step_completed")
                progress = completed / len(definition.steps)
            backend.update(
                str(job.id),
                events=events,
                progress=progress,
                message=event.event,
            )

        def persist_checkpoint(state: dict[str, Any], steps: tuple[StepRun, ...]) -> None:
            backend.update(
                str(job.id),
                checkpoint={
                    "state": _json_safe(state),
                    "steps": [step.model_dump(mode="json") for step in steps],
                },
            )

        try:
            run = self.run(
                definition,
                inputs=job.inputs,
                parameters=job.parameters,
                progress=persist_event,
                _run_id=job.run_id,
                _resume_checkpoint=job.checkpoint,
                _checkpoint=persist_checkpoint,
            )
            current = backend.get(str(job.id))
            if current is not None and current.status in {"CANCELLING", "CANCELLED"}:
                raise _PipelineCancelled()
            backend.update(
                str(job.id),
                status="COMPLETED",
                progress=1.0,
                message="Completed",
                result=run.model_dump(mode="json"),
                completed_at=_now(),
            )
        except _PipelineCancelled:
            backend.update(
                str(job.id),
                status="CANCELLED",
                message="Cancelled",
                completed_at=_now(),
            )
        except Exception as exc:
            current = backend.get(str(job.id))
            if current is not None and current.status in {"CANCELLING", "CANCELLED"}:
                backend.update(
                    str(job.id),
                    status="CANCELLED",
                    message="Cancelled",
                    completed_at=_now(),
                )
            else:
                run = getattr(exc, "run", None)
                backend.update(
                    str(job.id),
                    status="FAILED",
                    message="Failed",
                    result=(run.model_dump(mode="json") if run is not None else None),
                    error=str(exc),
                    completed_at=_now(),
                )
        finally:
            with self._worker_lock:
                self._workers.pop(job.id, None)

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
        cache_key: str | None = None
        if step.cacheable:
            cache_context = StepContext(
                knowledge_base=self._knowledge_base,
                run_id=run_id,
                pipeline_name=pipeline.name,
                step_name=step.name,
                parameters=parameters,
                attempt=0,
            )
            components = step.cache_key(cache_context, state)
            if not isinstance(components, CacheKeyComponents):
                components = CacheKeyComponents.model_validate(components)
            cache_payload = {
                "step_name": step.name,
                "step_version": step.version,
                **components.model_dump(mode="json"),
            }
            cache_key = hashlib.sha256(
                json.dumps(cache_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            with self._cache_lock:
                cached_output = self._cache.get(cache_key)
            if cached_output is not None:
                self._emit(progress, run_id, "step_cache_hit", step.name, 0)
                return StepRun(
                    name=step.name,
                    version=step.version,
                    status="COMPLETED",
                    attempts=0,
                    deterministic=True,
                    cacheable=True,
                    cached=True,
                    cache_key=cache_key,
                    required_capabilities=tuple(sorted(step.required_capabilities)),
                    side_effects=tuple(sorted(step.side_effects)),
                    timeout_seconds=step.timeout_seconds,
                    output=dict(cached_output),
                    started_at=started_at,
                    completed_at=_now(),
                )
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
                _event_sink=lambda event, message, level, payload: self._emit(
                    progress,
                    run_id,
                    event,
                    step.name,
                    attempt,
                    message,
                    level,
                    payload,
                ),
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
                    cache_key=cache_key,
                    required_capabilities=tuple(sorted(step.required_capabilities)),
                    side_effects=tuple(sorted(step.side_effects)),
                    timeout_seconds=step.timeout_seconds,
                    output=output_data,
                    started_at=started_at,
                    completed_at=_now(),
                )
                self._emit(progress, run_id, "step_completed", step.name, attempt)
                if cache_key is not None:
                    with self._cache_lock:
                        self._cache[cache_key] = dict(output_data)
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
            cache_key=cache_key,
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
        level: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if progress is not None:
            progress(
                ProgressEvent(
                    run_id=run_id,
                    event=event,
                    step_name=step_name,
                    attempt=attempt,
                    message=message,
                    level=level,
                    payload=payload or {},
                )
            )
