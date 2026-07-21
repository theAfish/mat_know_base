import uuid
from types import SimpleNamespace

import pytest

from mkb import ValidationError
from mkb.application_services import (
    AssistantService,
    MaintenanceService,
    SettingsService,
)
from mkb.web._models import AssistantChatRequest, SettingsUpdateRequest
from mkb.web.routers import assistant as assistant_router
from mkb.web.routers import settings as settings_router


class _Jobs:
    def __init__(self):
        self.calls = []

    def submit_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return SimpleNamespace(id=uuid.uuid4())


def test_settings_service_uses_injected_runtime_adapter():
    updates = []
    service = SettingsService(
        SimpleNamespace(),
        runtime_reader=lambda: {"log_level": "INFO"},
        runtime_updater=lambda values: updates.append(values)
        or {"log_level": values["log_level"]},
    )

    assert service.runtime() == {"log_level": "INFO"}
    assert service.update({"log_level": "DEBUG"}) == {"log_level": "DEBUG"}
    assert updates == [{"log_level": "DEBUG"}]


def test_operational_services_use_injected_application_adapters():
    settings = SettingsService(
        SimpleNamespace(),
        startup_validator=lambda **values: [f"host={values['host']}"],
    )
    cleanup_calls = []
    maintenance = MaintenanceService(
        SimpleNamespace(),
        migration_inventory_reader=lambda: {"tables": {"projects": 2}},
        cleanup_executor=lambda **values: cleanup_calls.append(values)
        or {"local": {"applied": values["apply"]}, "jobs": {}},
    )

    assert settings.validate_startup(host="0.0.0.0") == ["host=0.0.0.0"]
    assert maintenance.migration_inventory().data["tables"]["projects"] == 2
    assert maintenance.cleanup(
        older_than_days=5,
        job_days=10,
        apply=True,
        confirm="DELETE",
    ).data["local"] == {"applied": True}
    assert cleanup_calls == [
        {
            "older_than_days": 5,
            "job_days": 10,
            "apply": True,
            "confirm": "DELETE",
        }
    ]

    with pytest.raises(ValidationError, match="confirm='DELETE'"):
        maintenance.cleanup(apply=True, confirm="yes")


def test_assistant_service_owns_lazy_session_and_dispatches_through_jobs():
    jobs = _Jobs()
    sessions = []
    kb = SimpleNamespace(jobs=jobs)
    service = AssistantService(
        kb,
        session_factory=lambda: sessions.append("created") or ("runner", "session-1"),
        pending_workflows=lambda: [
            {
                "kind": "extraction",
                "project_id": "project-1",
                "kwargs": {"project_id": "project-1"},
            }
        ],
        action_resolver=lambda kind: {"extraction": "extract_project"}[kind],
    )

    first = service.chat("  explain this  ")
    service.chat("continue")

    assert first.id
    assert sessions == ["created"]
    assert jobs.calls[0][0] == "assistant_chat"
    assert jobs.calls[0][1]["message"] == "explain this"
    jobs.calls[0][1]["dispatch_pending_workflows"]()
    assert jobs.calls[-1] == (
        "extract_project",
        {
            "job_project_id": "project-1",
            "label": "extraction",
            "project_id": "project-1",
        },
    )

    with pytest.raises(ValidationError):
        service.chat("   ")


def test_settings_and_assistant_routes_use_application_services(monkeypatch):
    jobs = _Jobs()
    settings_service = SimpleNamespace(
        runtime=lambda: {"log_level": "INFO"},
        update=lambda values: dict(values),
    )
    assistant_service = SimpleNamespace(
        chat=lambda message: jobs.submit_action("assistant_chat", message=message)
    )
    kb = SimpleNamespace(settings=settings_service, assistant=assistant_service)
    monkeypatch.setattr(settings_router, "get_knowledge_base", lambda: kb)
    monkeypatch.setattr(assistant_router, "get_knowledge_base", lambda: kb)
    logging_calls = []
    monkeypatch.setattr(
        settings_router,
        "setup_logging",
        lambda **kwargs: logging_calls.append(kwargs),
    )

    assert settings_router.get_settings_endpoint() == {"log_level": "INFO"}
    assert settings_router.update_settings_endpoint(
        SettingsUpdateRequest(log_level="DEBUG")
    ) == {"log_level": "DEBUG"}
    response = assistant_router.assistant_chat(
        AssistantChatRequest(message="hello")
    )

    assert uuid.UUID(response["job_id"])
    assert jobs.calls[-1][1]["message"] == "hello"
    assert logging_calls == [{"level": "DEBUG", "force": True}]
