import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from mkb.adapters import SQLAlchemyLegacyJobBackend


def test_legacy_job_backend_maps_existing_application_job_losslessly():
    identifier = uuid.uuid4()
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        job_id=identifier,
        kind="extract",
        status="COMPLETED",
        label="Extract",
        project_id="project",
        request_id="request",
        active_key=None,
        idempotency_key="once",
        events=[{"message": "Done"}],
        attempt_count=1,
        max_attempts=2,
        retryable=True,
        cancel_requested=False,
        current_message="Done",
        result={"count": 1},
        error=None,
        error_category=None,
        created_at=now,
        queued_at=now,
        started_at=now,
        finished_at=now,
        updated_at=now,
    )

    job = SQLAlchemyLegacyJobBackend._model(row)

    assert job.id == identifier
    assert job.result == {"count": 1}
    assert job.events == ({"message": "Done"},)
    assert job.completed_at == now
