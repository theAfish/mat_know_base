import sys
from types import SimpleNamespace

import pytest

from mkb.web import job_actions


def test_job_action_start_params_resolves_api_target_lazily(monkeypatch):
    fake_process = object()
    monkeypatch.setitem(sys.modules, "mkb.api", SimpleNamespace(process=fake_process))

    params = job_actions.job_action_start_params(
        "process_project",
        job_project_id="project-1",
        project_id="project-1",
    )

    assert params["kind"] == "process"
    assert params["label"] == "Process"
    assert params["project_id"] == "project-1"
    assert params["target"] is fake_process
    assert params["kwargs"] == {"project_id": "project-1"}


def test_workflow_kind_maps_to_registry_action():
    assert job_actions.action_for_workflow_kind("projection_review") == "review_projection"


def test_job_action_prefers_injected_application_service_target():
    target = object()
    services = SimpleNamespace(service=lambda name: target if name == "process" else None)

    params = job_actions.job_action_start_params(
        "process_project",
        job_project_id="project-1",
        project_id="project-1",
        services=services,
    )

    assert params["target"] is target


def test_start_job_action_applies_project_conflict_policy(monkeypatch):
    def fake_process(**_kwargs):
        return {"ok": True}

    active = {"status": "RUNNING"}
    manager = SimpleNamespace(
        find_active_job=lambda **_kwargs: active,
        start_job=lambda **_kwargs: "job-1",
    )
    monkeypatch.setitem(sys.modules, "mkb.api", SimpleNamespace(process=fake_process))

    with pytest.raises(job_actions.JobActionConflict):
        job_actions.start_job_action(
            manager,
            "process_project",
            job_project_id="project-1",
            project_id="project-1",
        )
