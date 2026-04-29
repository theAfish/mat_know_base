import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from mkb import api
from mkb.agents.runner import AgentRunner


def test_api_process_forwards_progress_callback(monkeypatch):
    project_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    events = []

    fake_session = MagicMock()
    fake_session.query.return_value.filter_by.return_value.all.return_value = [
        SimpleNamespace(asset_id=asset_id)
    ]
    fake_context_manager = MagicMock()
    fake_context_manager.__enter__.return_value = fake_session
    fake_context_manager.__exit__.return_value = False

    monkeypatch.setattr(api, "SyncSessionLocal", lambda: fake_context_manager)

    def fake_process_asset(aid, progress_callback=None):
        assert aid == asset_id
        assert progress_callback is not None
        progress_callback({"message": "Downloaded paper.pdf", "asset_id": str(aid)})
        return {"asset_id": str(aid), "status": "SUCCESS"}

    monkeypatch.setattr("mkb.processors.coordinator.process_asset", fake_process_asset)

    result = api.process(project_id=project_id, progress_callback=events.append)

    assert result["assets_processed"] == 1
    assert result["results"][0]["status"] == "SUCCESS"
    assert [event["message"] for event in events] == [
        "Starting asset 1/1",
        "Downloaded paper.pdf",
    ]


def test_agent_runner_reports_text_and_tool_progress():
    progress_events = []

    async def fake_run_async(**_kwargs):
        yield SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="Planning extraction", function_call=None)]
            ),
            is_final_response=lambda: False,
        )
        yield SimpleNamespace(
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        text=None,
                        function_call=SimpleNamespace(name="read_project_files", args={"project_id": "p1"}),
                    )
                ]
            ),
            is_final_response=lambda: False,
        )
        yield SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="Done", function_call=None)]
            ),
            is_final_response=lambda: True,
        )

    runner = object.__new__(AgentRunner)
    runner.runner = SimpleNamespace(run_async=fake_run_async)

    result = asyncio.run(
        runner.run(
            session_id="session-1",
            message="extract",
            progress_callback=progress_events.append,
        )
    )

    assert result.success is True
    assert result.final_text == "Done"
    assert [event["message"] for event in progress_events] == [
        "Planning extraction",
        "Agent called read_project_files",
        "Done",
    ]