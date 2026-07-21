import uuid
from datetime import datetime, timezone

from mkb import Jobs
from mkb.web.job_backend import WebJobBackend, serialize_web_job


class _Manager:
    def __init__(self):
        now = datetime.now(timezone.utc).isoformat()
        self.rows = {
            str(uuid.uuid4()): {
                "job_id": None,
                "kind": "projection_review",
                "label": "Review",
                "status": "RUNNING",
                "project_id": "project-1",
                "attempt_count": 1,
                "max_attempts": 2,
                "retryable": True,
                "cancel_requested": False,
                "current_message": "Reviewing",
                "events": [{"message": "Started", "timestamp": now}],
                "result": None,
                "error": None,
                "created_at": now,
                "queued_at": now,
                "started_at": now,
                "finished_at": None,
                "updated_at": now,
            }
        }
        identifier, row = next(iter(self.rows.items()))
        row["job_id"] = identifier

    def get_job(self, job_id):
        return self.rows.get(str(job_id))

    def list_jobs(self, *, limit, project_id=None):
        rows = list(self.rows.values())
        if project_id is not None:
            rows = [row for row in rows if row["project_id"] == project_id]
        return rows[:limit]

    def cancel_job(self, job_id):
        row = self.rows.get(str(job_id))
        if row is None:
            return False
        row["status"] = "CANCELLING"
        row["cancel_requested"] = True
        return True

    def cancel_all_active(self, *, project_id=None):
        identifiers = [
            identifier
            for identifier, row in self.rows.items()
            if project_id is None or row["project_id"] == project_id
        ]
        for identifier in identifiers:
            self.cancel_job(identifier)
        return identifiers

    def find_active_job(self, *, project_id=None, kind=None):
        for row in self.rows.values():
            if project_id is not None and row["project_id"] != project_id:
                continue
            if kind is not None and row["kind"] != kind:
                continue
            if row["status"] in {"QUEUED", "RUNNING", "CANCELLING"}:
                return row
        return None

    def recover_interrupted(self):
        return 0


def test_web_job_backend_preserves_react_payload_and_grouped_operations():
    manager = _Manager()
    service = Jobs(WebJobBackend(manager))
    job = service.list(project_id="project-1")[0]

    payload = serialize_web_job(job)

    assert payload["job_id"] == str(job.id)
    assert payload["project_id"] == "project-1"
    assert payload["current_message"] == "Reviewing"
    assert payload["events"][0]["message"] == "Started"
    assert service.find_active(project_id="project-1", kind="projection_review") == job

    cancelled = service.cancel_all(project_id="project-1")
    assert [item.status for item in cancelled] == ["CANCELLING"]


def test_web_job_action_submission_returns_typed_job():
    manager = _Manager()
    identifier = next(iter(manager.rows))
    submitted = []
    service = Jobs(
        WebJobBackend(manager),
        action_submitter=lambda action, **kwargs: (
            submitted.append((action, kwargs)) or identifier
        ),
    )

    job = service.submit_action("review_graph", mode="all")

    assert job.id == uuid.UUID(identifier)
    assert submitted == [("review_graph", {"mode": "all"})]
