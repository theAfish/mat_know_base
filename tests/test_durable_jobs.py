from mkb.jobs import JobConflict, MemoryJobStore


def _job(job_id="00000000-0000-0000-0000-000000000001", **overrides):
    row = {
        "job_id": job_id, "kind": "process", "label": "Process", "status": "QUEUED",
        "project_id": "p1", "idempotency_key": "request-1", "active_key": "project:p1:process",
        "attempt_count": 0, "max_attempts": 1, "retryable": False,
        "cancel_requested": False, "current_message": "Queued", "events": [],
        "result": None, "error": None, "error_category": None,
        "created_at": "2026-01-01T00:00:00+00:00", "queued_at": "2026-01-01T00:00:00+00:00",
        "started_at": None, "finished_at": None, "updated_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_store_enforces_idempotency_and_active_work():
    store = MemoryJobStore()
    store.create(_job())
    try:
        store.create(_job("00000000-0000-0000-0000-000000000002", active_key="other"))
    except JobConflict as exc:
        assert exc.existing["job_id"].endswith("1")
    else:
        raise AssertionError("duplicate idempotency key was accepted")


def test_restart_marks_active_jobs_interrupted_and_releases_lock():
    store = MemoryJobStore()
    store.create(_job())
    assert store.recover_interrupted() == 1
    row = store.get("00000000-0000-0000-0000-000000000001")
    assert row["status"] == "INTERRUPTED"
    assert row["active_key"] is None
    assert row["error_category"] == "worker_interrupted"
