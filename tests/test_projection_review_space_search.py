import importlib
import mkb
import sys
from types import SimpleNamespace

import pytest

from mkb.web._models import ProjectionReviewRequest


SPACE_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
OTHER_PROJECT_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def projections_router(monkeypatch):
    fake_api = SimpleNamespace(
        review_projections=object(),
        review_projections_all=object(),
        review_projections_session=object(),
    )
    fake_jobs = SimpleNamespace(start_job=lambda **kwargs: "job")

    module_name = "mkb.web.routers.projections"
    sys.modules.pop(module_name, None)
    monkeypatch.setitem(sys.modules, "mkb.api", fake_api)
    monkeypatch.setattr(mkb, "api", fake_api, raising=False)
    monkeypatch.setitem(sys.modules, "mkb.web._state", SimpleNamespace(jobs=fake_jobs))

    module = importlib.import_module(module_name)
    yield module

    sys.modules.pop(module_name, None)
    routers_pkg = sys.modules.get("mkb.web.routers")
    if routers_pkg is not None and hasattr(routers_pkg, "projections"):
        delattr(routers_pkg, "projections")


def test_projection_review_single_project_uses_space_search_settings(
    monkeypatch,
    projections_router,
):
    calls = []

    def fake_start_job(**kwargs):
        calls.append(kwargs)
        return "job-1"

    monkeypatch.setattr(projections_router.jobs, "start_job", fake_start_job)

    result = projections_router.review_projections(
        ProjectionReviewRequest(space_id=SPACE_ID, project_id=PROJECT_ID)
    )

    assert result == {"job_id": "job-1"}
    assert calls[0]["target"] is projections_router.api.review_projections
    assert "allow_search" not in calls[0]["kwargs"]


def test_projection_review_all_projects_uses_space_search_settings(
    monkeypatch,
    projections_router,
):
    calls = []

    def fake_start_job(**kwargs):
        calls.append(kwargs)
        return "job-2"

    monkeypatch.setattr(projections_router.jobs, "start_job", fake_start_job)

    projections_router.review_projections(ProjectionReviewRequest(space_id=SPACE_ID))

    assert calls[0]["target"] is projections_router.api.review_projections_all
    assert calls[0]["kwargs"]["project_ids"] is None
    assert "allow_search" not in calls[0]["kwargs"]


def test_projection_review_selected_projects_starts_isolated_jobs(
    monkeypatch,
    projections_router,
):
    calls = []

    def fake_start_job(**kwargs):
        calls.append(kwargs)
        return f"job-{len(calls)}"

    monkeypatch.setattr(projections_router.jobs, "start_job", fake_start_job)

    result = projections_router.review_projections(
        ProjectionReviewRequest(
            space_id=SPACE_ID,
            project_ids=[PROJECT_ID, OTHER_PROJECT_ID],
        )
    )

    assert result == {"job_id": "job-1", "job_ids": ["job-1", "job-2"]}
    assert [call["target"] for call in calls] == [
        projections_router.api.review_projections,
        projections_router.api.review_projections,
    ]
    assert [call["project_id"] for call in calls] == [PROJECT_ID, OTHER_PROJECT_ID]
    assert [call["kwargs"]["project_id"] for call in calls] == [PROJECT_ID, OTHER_PROJECT_ID]
    assert all("allow_search" not in call["kwargs"] for call in calls)


def test_projection_review_session_uses_space_search_settings(
    monkeypatch,
    projections_router,
):
    calls = []

    def fake_start_job(**kwargs):
        calls.append(kwargs)
        return "job-3"

    monkeypatch.setattr(projections_router.jobs, "start_job", fake_start_job)

    projections_router.review_projections(
        ProjectionReviewRequest(
            space_id=SPACE_ID,
            project_ids=[PROJECT_ID, OTHER_PROJECT_ID],
            mode="session",
        )
    )

    assert calls[0]["target"] is projections_router.api.review_projections_session
    assert calls[0]["kwargs"]["project_ids"] == [PROJECT_ID, OTHER_PROJECT_ID]
    assert "allow_search" not in calls[0]["kwargs"]
