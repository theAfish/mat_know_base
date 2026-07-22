import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from mkb import api
from mkb.agents.runner import AgentRunner, RunResult, _is_retryable_provider_error


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

    database = SimpleNamespace(session=lambda: fake_context_manager)
    object_store = SimpleNamespace()

    def fake_process_asset(aid, *, database, object_store, processed_bucket, progress_callback=None):
        assert aid == asset_id
        assert processed_bucket == "processed"
        assert progress_callback is not None
        progress_callback({"message": "Downloaded paper.pdf", "asset_id": str(aid)})
        return {"asset_id": str(aid), "status": "SUCCESS"}

    monkeypatch.setattr("mkb.processors.coordinator.process_asset", fake_process_asset)
    from mkb.services.content_operations import ContentOperations

    monkeypatch.setattr(
        "mkb.services.compatibility_resources.content_operations",
        lambda: ContentOperations(
            database,
            object_store,
            raw_bucket="raw",
            processed_bucket="processed",
        ),
    )

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


def test_agent_runner_uses_latest_text_when_no_final_response_flag():
    progress_events = []

    async def fake_run_async(**_kwargs):
        yield SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="I am your MKB assistant.", function_call=None)]
            ),
            is_final_response=lambda: False,
        )
        yield SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="How can I help next?", function_call=None)]
            ),
            is_final_response=lambda: False,
        )

    runner = object.__new__(AgentRunner)
    runner.runner = SimpleNamespace(run_async=fake_run_async)

    result = asyncio.run(
        runner.run(
            session_id="session-2",
            message="who are you",
            progress_callback=progress_events.append,
        )
    )

    assert result.success is True
    assert result.final_text == "How can I help next?"
    assert [event["message"] for event in progress_events] == [
        "I am your MKB assistant.",
        "How can I help next?",
    ]


def test_agent_runner_reports_retry_progress_for_transient_errors():
    progress_events = []
    attempts = []

    async def fake_run_once(**_kwargs):
        attempts.append("x")
        if len(attempts) == 1:
            return RunResult(success=False, error="Timeout on reading data from socket")
        return RunResult(success=True, final_text="Recovered")

    runner = object.__new__(AgentRunner)
    runner._run_once = fake_run_once

    result = asyncio.run(
        runner.run(
            session_id="session-3",
            message="extract",
            progress_callback=progress_events.append,
            max_retries=2,
            retry_delay=0,
        )
    )

    assert result.success is True
    assert result.final_text == "Recovered"
    assert len(attempts) == 2
    assert progress_events == [
        {
            "label": "retry",
            "message": (
                "Transient model error on attempt 1/2: Timeout on reading data from socket. "
                "Retrying in 0.0s."
            ),
            "stage": "retry",
        }
    ]


def test_retryable_provider_error_includes_tool_call_json_parse_failures():
    assert _is_retryable_provider_error(
        "JSONDecodeError: Expecting ',' delimiter while parsing tool call arguments"
    ) is True
    assert _is_retryable_provider_error(
        "Expecting property name enclosed in double quotes: line 1 column 3401"
    ) is True
