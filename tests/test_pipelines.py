from types import SimpleNamespace
import time
import uuid

import pytest
from pydantic import BaseModel

from mkb import (
    CacheKeyComponents,
    ConflictError,
    KnowledgeBase,
    Pipeline,
    PipelineExecutionError,
    RetryPolicy,
    Record,
    Step,
    ValidationError,
)


def _client(**kwargs):
    return KnowledgeBase(services=SimpleNamespace(), **kwargs)


def test_sequential_pipeline_merges_outputs_and_emits_typed_events():
    kb = _client()
    events = []
    pipeline = Pipeline(
        name="consumer-pipeline",
        version="2",
        steps=(
            Step(name="normalize", handler=lambda _context, state: {"value": state["x"] + 1}),
            Step(
                name="format",
                handler=lambda context, state: {
                    "text": f"{state['value']}:{context.parameters['suffix']}"
                },
            ),
        ),
    )

    kb.pipelines.register(pipeline)
    run = kb.pipelines.run(
        "consumer-pipeline",
        inputs={"x": 2},
        parameters={"suffix": "ok"},
        progress=events.append,
    )

    assert run.status == "COMPLETED"
    assert run.outputs == {"x": 2, "value": 3, "text": "3:ok"}
    assert [step.name for step in run.steps] == ["normalize", "format"]
    assert [event.event for event in events] == [
        "pipeline_started",
        "step_started",
        "step_completed",
        "step_started",
        "step_completed",
        "pipeline_completed",
    ]
    assert kb.pipelines.get_run(run.id) == run
    assert run.model_dump(mode="json")["id"] == str(run.id)


def test_pipeline_registry_is_per_client_and_rejects_duplicate_names():
    first = _client()
    second = _client()
    pipeline = Pipeline(name="only-first", steps=(Step(name="one", handler=lambda _c, _s: {}),))

    first.pipelines.register(pipeline)

    assert first.pipelines.get("only-first") is pipeline
    assert second.pipelines.get("only-first") is None
    with pytest.raises(ConflictError, match="already registered"):
        first.pipelines.register(pipeline)


def test_pipeline_retries_and_exposes_failed_run_on_typed_error():
    kb = _client()
    attempts = []

    def fail(context, _state):
        attempts.append(context.attempt)
        raise RuntimeError("provider unavailable")

    pipeline = Pipeline(
        name="failure",
        steps=(Step(name="fail", handler=fail, retry=RetryPolicy(max_attempts=2)),),
    )

    with pytest.raises(PipelineExecutionError, match="2 attempt") as captured:
        kb.pipelines.run(pipeline)

    assert attempts == [1, 2]
    assert captured.value.run.status == "FAILED"
    assert captured.value.run.steps[0].attempts == 2
    assert kb.pipelines.get_run(captured.value.run.id) == captured.value.run


def test_pipeline_capabilities_are_checked_before_any_step_runs():
    called = False

    def handler(_context, _state):
        nonlocal called
        called = True
        return {}

    pipeline = Pipeline(
        name="graph",
        steps=(
            Step(
                name="traverse",
                handler=handler,
                required_capabilities=frozenset({"graph_traversal"}),
            ),
        ),
    )

    with pytest.raises(PipelineExecutionError, match="graph_traversal"):
        _client().pipelines.run(pipeline)

    assert called is False


def test_steps_validate_typed_contracts_and_accept_existing_records():
    class Input(BaseModel):
        record: Record

    class Parameters(BaseModel):
        field: str

    class Output(BaseModel):
        selected: object

    record = Record(
        id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        status="COMPLETED",
        data={"material": "nickelate"},
    )
    pipeline = Pipeline(
        name="reuse-existing-record",
        steps=(
            Step(
                name="select",
                input_model=Input,
                parameter_model=Parameters,
                output_model=Output,
                handler=lambda context, state: {
                    "selected": state["record"].data[context.parameters["field"]]
                },
            ),
        ),
    )

    run = _client().pipelines.run(
        pipeline,
        inputs={"record": record},
        parameters={"field": "material"},
    )

    assert run.outputs["selected"] == "nickelate"
    assert run.model_dump(mode="json")["inputs"]["record"]["id"] == str(record.id)

    with pytest.raises(PipelineExecutionError, match="validation error"):
        _client().pipelines.run(pipeline, inputs={"wrong": record})


def test_pipeline_definitions_and_run_ids_use_public_validation_errors():
    with pytest.raises(ValidationError, match="deterministic"):
        Step(name="bad-cache", handler=lambda _c, _s: {}, cacheable=True)
    with pytest.raises(ValidationError, match="cache_key"):
        Step(
            name="missing-cache-key",
            handler=lambda _c, _s: {},
            deterministic=True,
            cacheable=True,
        )
    with pytest.raises(ValidationError, match="at least 1"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValidationError, match="UUID"):
        _client().pipelines.get_run("bad-id")


def test_deterministic_cache_key_contains_all_required_identity_components():
    calls = []

    def handler(_context, state):
        calls.append(state["value"])
        return {"normalized": state["value"].lower()}

    def cache_key(context, state):
        return CacheKeyComponents(
            configuration={"mode": context.parameters["mode"]},
            source_fingerprint=state["fingerprint"],
            model_identity=context.parameters["model"],
            schema_version=context.parameters["schema_version"],
        )

    pipeline = Pipeline(
        name="cached",
        steps=(
            Step(
                name="normalize",
                version="3",
                handler=handler,
                deterministic=True,
                cacheable=True,
                cache_key=cache_key,
            ),
        ),
    )
    kb = _client()
    parameters = {"mode": "strict", "model": "provider/model", "schema_version": "7"}

    first = kb.pipelines.run(
        pipeline,
        inputs={"value": "CALCITE", "fingerprint": "sha256:one"},
        parameters=parameters,
    )
    second = kb.pipelines.run(
        pipeline,
        inputs={"value": "CALCITE", "fingerprint": "sha256:one"},
        parameters=parameters,
    )
    changed_schema = kb.pipelines.run(
        pipeline,
        inputs={"value": "CALCITE", "fingerprint": "sha256:one"},
        parameters={**parameters, "schema_version": "8"},
    )

    assert calls == ["CALCITE", "CALCITE"]
    assert first.steps[0].cached is False
    assert second.steps[0].cached is True
    assert second.steps[0].attempts == 0
    assert second.steps[0].cache_key == first.steps[0].cache_key
    assert changed_schema.steps[0].cache_key != first.steps[0].cache_key


def test_pipeline_dependencies_are_validated_and_run_in_topological_order():
    calls = []
    pipeline = Pipeline(
        name="dag",
        steps=(
            Step(
                name="finish",
                depends_on=frozenset({"prepare"}),
                handler=lambda _context, state: (
                    calls.append("finish") or {"result": state["prepared"] + 1}
                ),
            ),
            Step(
                name="prepare",
                handler=lambda _context, _state: (
                    calls.append("prepare") or {"prepared": 4}
                ),
            ),
        ),
    )

    run = _client().pipelines.run(pipeline)

    assert calls == ["prepare", "finish"]
    assert [step.name for step in run.steps] == ["prepare", "finish"]
    assert run.outputs["result"] == 5
    with pytest.raises(ValidationError, match="unknown"):
        Pipeline(
            name="unknown-dependency",
            steps=(
                Step(
                    name="one",
                    depends_on=frozenset({"missing"}),
                    handler=lambda _context, _state: {},
                ),
            ),
        )
    with pytest.raises(ValidationError, match="cycle"):
        Pipeline(
            name="cycle",
            steps=(
                Step(
                    name="one",
                    depends_on=frozenset({"two"}),
                    handler=lambda _context, _state: {},
                ),
                Step(
                    name="two",
                    depends_on=frozenset({"one"}),
                    handler=lambda _context, _state: {},
                ),
            ),
        )


def test_step_timeout_fails_promptly_and_signals_cooperative_handler():
    observed_cancellation = []

    def slow(context, _state):
        while not context.cancelled:
            time.sleep(0.002)
        observed_cancellation.append(True)
        context.check_cancelled()

    pipeline = Pipeline(
        name="timeout",
        steps=(Step(name="slow", handler=slow, timeout_seconds=0.02),),
    )
    started = time.monotonic()

    with pytest.raises(PipelineExecutionError, match="exceeded timeout"):
        _client().pipelines.run(pipeline)

    assert time.monotonic() - started < 0.2
    deadline = time.monotonic() + 0.2
    while not observed_cancellation and time.monotonic() < deadline:
        time.sleep(0.002)
    assert observed_cancellation == [True]
