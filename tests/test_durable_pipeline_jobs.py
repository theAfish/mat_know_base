import threading
import uuid
from datetime import datetime, timezone

from mkb import Job, KnowledgeBase, Pipeline, Step


def _client(tmp_path, name="jobs.db"):
    return KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / name}")


def test_submit_is_persisted_idempotent_and_uses_stable_run_id(tmp_path):
    def double(context, state):
        context.emit("halfway", input_value=state["value"])
        context.log("doubling value", level="debug", operation="multiply")
        return {"value": state["value"] * 2}

    pipeline = Pipeline(
        name="durable",
        version="2",
        steps=(
            Step(name="double", handler=double),
        ),
    )
    with _client(tmp_path) as kb:
        kb.initialize()
        kb.pipelines.register(pipeline)

        submitted = kb.pipelines.submit(
            "durable",
            inputs={"value": 6},
            idempotency_key="durable:sample-1",
        )
        duplicate = kb.pipelines.submit(
            "durable",
            inputs={"value": 999},
            idempotency_key="durable:sample-1",
        )
        completed = kb.jobs.wait(submitted.id, timeout=5)

        assert isinstance(submitted, Job)
        assert duplicate.id == submitted.id
        assert completed.status == "COMPLETED"
        assert completed.result["id"] == str(submitted.run_id)
        assert completed.result["outputs"]["value"] == 12
        assert completed.checkpoint["state"]["value"] == 12
        assert completed.checkpoint["steps"][0]["name"] == "double"
        assert completed.progress == 1.0
        assert any(event["event"] == "step_completed" for event in completed.events)
        progress_event = next(
            event for event in completed.events if event["event"] == "step_progress"
        )
        log_event = next(event for event in completed.events if event["event"] == "step_log")
        assert progress_event["payload"] == {"input_value": 6}
        assert log_event["level"] == "DEBUG"
        assert log_event["payload"] == {"operation": "multiply"}
        job_id = submitted.id

    with _client(tmp_path) as reopened:
        reopened.initialize()
        persisted = reopened.jobs.require(job_id)
        assert persisted.status == "COMPLETED"
        assert persisted.result["outputs"]["value"] == 12


def test_resume_uses_checkpoint_without_repeating_completed_steps(tmp_path):
    calls = {"prepare": 0, "finish": 0}

    def prepare(_context, state):
        calls["prepare"] += 1
        return {"prepared": state["value"] + 1}

    def finish(_context, state):
        calls["finish"] += 1
        if calls["finish"] == 1:
            raise RuntimeError("temporary provider failure")
        return {"finished": state["prepared"] * 3}

    pipeline = Pipeline(
        name="resumable",
        steps=(
            Step(name="prepare", handler=prepare),
            Step(name="finish", handler=finish),
        ),
    )
    with _client(tmp_path, "resume.db") as kb:
        kb.initialize()
        kb.pipelines.register(pipeline)
        submitted = kb.pipelines.submit(pipeline, inputs={"value": 4})
        failed = kb.jobs.wait(submitted.id, timeout=5)

        assert failed.status == "FAILED"
        assert [step["name"] for step in failed.checkpoint["steps"]] == ["prepare"]
        original_run_id = failed.run_id

        resumed = kb.pipelines.resume(failed.id)
        completed = kb.jobs.wait(resumed.id, timeout=5)

        assert completed.status == "COMPLETED"
        assert completed.run_id == original_run_id
        assert completed.result["outputs"]["finished"] == 15
        assert calls == {"prepare": 1, "finish": 2}
        assert completed.attempt_count == 2


def test_running_job_cancellation_is_observed_at_step_boundary(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def blocking(_context, _state):
        entered.set()
        assert release.wait(timeout=5)
        return {"first": True}

    pipeline = Pipeline(
        name="cancel",
        steps=(
            Step(name="blocking", handler=blocking),
            Step(name="must-not-run", handler=lambda _context, _state: {"second": True}),
        ),
    )
    with _client(tmp_path, "cancel.db") as kb:
        kb.initialize()
        submitted = kb.pipelines.submit(pipeline)
        assert entered.wait(timeout=5)

        requested = kb.jobs.cancel(submitted.id)
        release.set()
        cancelled = kb.jobs.wait(submitted.id, timeout=5)

        assert requested.status == "CANCELLING"
        assert cancelled.status == "CANCELLED"
        assert cancelled.completed_at is not None
        assert not any(
            event.get("step_name") == "must-not-run" for event in cancelled.events
        )


def test_interrupted_recovery_is_explicit_and_restartable(tmp_path):
    with _client(tmp_path, "interrupted.db") as kb:
        kb.initialize()
        abandoned = Job(
            id=uuid.uuid4(),
            kind="pipeline",
            status="RUNNING",
            pipeline_name="later",
            pipeline_version="1",
            run_id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        kb.job_backend.create(abandoned)

        assert kb.jobs.recover_interrupted() == 1
        recovered = kb.jobs.require(abandoned.id)
        assert recovered.status == "INTERRUPTED"
        assert recovered.completed_at is not None


def test_direct_job_submission_is_idempotent_and_events_are_streamable(tmp_path):
    with _client(tmp_path, "direct.db") as kb:
        kb.initialize()
        submitted = kb.jobs.submit(
            kind="consumer.operation",
            inputs={"record_id": "example"},
            idempotency_key="consumer:example",
        )
        duplicate = kb.jobs.submit(
            kind="consumer.operation",
            inputs={"record_id": "ignored"},
            idempotency_key="consumer:example",
        )
        kb.job_backend.update(
            str(submitted.id),
            status="COMPLETED",
            events=({"event": "completed", "sequence": 1},),
            completed_at=datetime.now(timezone.utc),
        )

        assert duplicate.id == submitted.id
        assert list(kb.jobs.events(submitted.id)) == [
            {"event": "completed", "sequence": 1}
        ]
        assert list(kb.jobs.events(submitted.id, after=1, follow=True)) == []
