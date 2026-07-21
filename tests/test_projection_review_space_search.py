import importlib
import mkb
import sys
import uuid
from types import SimpleNamespace

import pytest

from mkb.web._models import ProjectionReviewRequest


SPACE_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
OTHER_PROJECT_ID = "33333333-3333-3333-3333-333333333333"
JOB_IDS = (
    uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"),
    uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2"),
)


def _use_fake_jobs(monkeypatch, module, calls):
    class FakeJobs:
        def submit_action(self, action, **kwargs):
            calls.append({"action": action, **kwargs})
            return SimpleNamespace(id=JOB_IDS[len(calls) - 1])

    monkeypatch.setattr(
        module,
        "get_knowledge_base",
        lambda: SimpleNamespace(jobs=FakeJobs()),
    )


@pytest.fixture
def projections_router(monkeypatch):
    fake_api = SimpleNamespace(
        review_projections=object(),
        review_projections_all=object(),
        review_projections_session=object(),
    )
    module_name = "mkb.web.routers.projections"
    sys.modules.pop(module_name, None)
    monkeypatch.setitem(sys.modules, "mkb.api", fake_api)
    monkeypatch.setattr(mkb, "api", fake_api, raising=False)
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

    _use_fake_jobs(monkeypatch, projections_router, calls)

    result = projections_router.review_projections(
        ProjectionReviewRequest(space_id=SPACE_ID, project_id=PROJECT_ID)
    )

    assert result == {"job_id": str(JOB_IDS[0])}
    assert calls[0]["action"] == "review_projection"
    assert "allow_search" not in calls[0]


def test_projection_review_all_projects_uses_space_search_settings(
    monkeypatch,
    projections_router,
):
    calls = []

    _use_fake_jobs(monkeypatch, projections_router, calls)

    projections_router.review_projections(ProjectionReviewRequest(space_id=SPACE_ID))

    assert calls[0]["action"] == "review_projection_all"
    assert calls[0]["project_ids"] is None
    assert "allow_search" not in calls[0]


def test_projection_review_selected_projects_starts_isolated_jobs(
    monkeypatch,
    projections_router,
):
    calls = []

    _use_fake_jobs(monkeypatch, projections_router, calls)

    result = projections_router.review_projections(
        ProjectionReviewRequest(
            space_id=SPACE_ID,
            project_ids=[PROJECT_ID, OTHER_PROJECT_ID],
        )
    )

    identifiers = [str(identifier) for identifier in JOB_IDS]
    assert result == {"job_id": identifiers[0], "job_ids": identifiers}
    assert [call["action"] for call in calls] == [
        "review_projection",
        "review_projection",
    ]
    assert [call["job_project_id"] for call in calls] == [PROJECT_ID, OTHER_PROJECT_ID]
    assert [call["project_id"] for call in calls] == [PROJECT_ID, OTHER_PROJECT_ID]
    assert all("allow_search" not in call for call in calls)


def test_projection_review_session_uses_space_search_settings(
    monkeypatch,
    projections_router,
):
    calls = []

    _use_fake_jobs(monkeypatch, projections_router, calls)

    projections_router.review_projections(
        ProjectionReviewRequest(
            space_id=SPACE_ID,
            project_ids=[PROJECT_ID, OTHER_PROJECT_ID],
            mode="session",
        )
    )

    assert calls[0]["action"] == "review_projection_session"
    assert calls[0]["project_ids"] == [PROJECT_ID, OTHER_PROJECT_ID]
    assert "allow_search" not in calls[0]
