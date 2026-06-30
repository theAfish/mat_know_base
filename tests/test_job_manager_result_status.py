import importlib
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def web_state_module(monkeypatch):
    module_name = "mkb.web._state"
    sys.modules.pop(module_name, None)

    monkeypatch.setitem(sys.modules, "mkb.api", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "mkb.config", SimpleNamespace(settings=SimpleNamespace(max_concurrent_jobs=1)))
    monkeypatch.setitem(sys.modules, "mkb.agents._utils", SimpleNamespace(JobCancelled=RuntimeError))
    monkeypatch.setitem(sys.modules, "mkb.agents.orchestrator", SimpleNamespace(create_orchestrator_runner=lambda: None))
    monkeypatch.setitem(sys.modules, "mkb.agents.tools.orchestrator_tools", SimpleNamespace(get_pending_workflows=lambda: []))

    module = importlib.import_module(module_name)
    yield module

    sys.modules.pop(module_name, None)


def test_job_manager_marks_error_result_as_failed(web_state_module):
    manager = web_state_module.JobManager(max_concurrent=1)

    job_id = manager.start_job(
        kind="projection_review",
        label="Projection Review",
        target=lambda progress_callback=None: {
            "status": "error",
            "message": "No active projections available for review.",
        },
    )

    for _ in range(50):
        job = manager.get_job(job_id)
        if job and job["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.01)
    else:
        pytest.fail("job did not reach a terminal state")

    assert job is not None
    assert job["status"] == "FAILED"
    assert job["error"] == "No active projections available for review."
    assert job["result"]["status"] == "error"
