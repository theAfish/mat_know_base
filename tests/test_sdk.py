from types import SimpleNamespace

import pytest

from mkb import KnowledgeBase, MKBConfig


def _services(calls, identity):
    def operation(name, result=None):
        def invoke(*args, **kwargs):
            calls.append((identity, name, args, kwargs))
            return result

        return invoke

    return SimpleNamespace(
        setup=operation("setup"),
        ingest=operation("ingest", {"project_id": identity}),
        sync=operation("sync", {}),
        sync_project=operation("sync_project", {}),
        process=operation("process", {}),
        extract=operation("extract", {}),
        list_projects=operation("list_projects", [{"project_id": identity}]),
        list_assets=operation("list_assets", []),
        list_processed_assets=operation("list_processed_assets", []),
        list_frames=operation("list_frames", []),
        get_frame=operation("get_frame"),
        create_space=operation("create_space", {}),
        get_space=operation("get_space"),
        list_spaces=operation("list_spaces", []),
        project=operation("project", {}),
        get_projection=operation("get_projection"),
        list_projections=operation("list_projections", []),
    )


def test_clients_keep_service_bindings_and_configuration_isolated():
    calls = []
    first = KnowledgeBase(
        services=_services(calls, "first"),
        config=MKBConfig(database_url="sqlite:///first.db"),
    )
    second = KnowledgeBase(
        services=_services(calls, "second"),
        config=MKBConfig(database_url="sqlite:///second.db"),
    )

    assert first.ingest("a")["project_id"] == "first"
    assert second.list_projects()[0]["project_id"] == "second"
    assert first.config.database_url == "sqlite:///first.db"
    assert second.config.database_url == "sqlite:///second.db"
    assert [call[0] for call in calls] == ["first", "second"]


def test_client_delegates_lifecycle_arguments_explicitly():
    calls = []
    kb = KnowledgeBase(services=_services(calls, "kb"))

    kb.process(project_id="project", progress_callback="callback")

    assert calls[-1] == (
        "kb",
        "process",
        (),
        {"project_id": "project", "progress_callback": "callback"},
    )


def test_client_context_closes_and_rejects_further_calls():
    calls = []
    with KnowledgeBase(services=_services(calls, "kb")) as kb:
        assert kb.closed is False
    assert kb.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        kb.list_projects()
