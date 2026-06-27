"""Tests for TemporalWorkflowHistoryTool."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.tools.conftest import BaseToolContract, mock_agent_state
from tools.temporal_tools import TemporalWorkflowHistoryTool


class TestTemporalWorkflowHistoryToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return TemporalWorkflowHistoryTool()


def test_is_available_when_configured() -> None:
    tool = TemporalWorkflowHistoryTool()
    assert tool.is_available({"temporal": {"base_url": "http://localhost:7233"}}) is True


def test_is_available_when_not_configured() -> None:
    tool = TemporalWorkflowHistoryTool()
    assert tool.is_available({"temporal": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = TemporalWorkflowHistoryTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["base_url"] == "http://localhost:7233"
    assert params["namespace"] == "default"
    assert params["api_key"] == ""


def test_run_returns_unavailable_when_no_base_url() -> None:
    tool = TemporalWorkflowHistoryTool()
    result = tool.run(base_url="", workflow_id="wf-1")
    assert result["available"] is False
    assert "base_url is required" in result["error"]
    assert result["events"] == []


def test_run_returns_error_when_no_workflow_id() -> None:
    tool = TemporalWorkflowHistoryTool()
    result = tool.run(base_url="http://localhost:7233", workflow_id="")
    assert result["available"] is True
    assert "workflow_id is required" in result["error"]
    assert result["events"] == []


def test_run_happy_path(monkeypatch) -> None:
    tool = TemporalWorkflowHistoryTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get_workflow_history.return_value = {
        "success": True,
        "events": [
            {
                "eventId": "1",
                "eventTime": "2024-01-15T10:00:00Z",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
                "taskId": "1048576",
                "workerMayIgnore": False,
            },
            {
                "eventId": "2",
                "eventTime": "2024-01-15T10:00:05Z",
                "eventType": "EVENT_TYPE_ACTIVITY_TASK_FAILED",
                "taskId": "1048580",
                "workerMayIgnore": False,
            },
            {
                "eventId": "3",
                "eventTime": "2024-01-15T10:00:05Z",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
                "taskId": "1048581",
                "workerMayIgnore": False,
            },
        ],
        "next_page_token": "",
        "archived": False,
        "total": 3,
    }

    monkeypatch.setattr(
        "tools.temporal_tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(
        base_url="http://localhost:7233",
        workflow_id="wf-1",
        run_id="run-1",
        namespace="default",
    )
    assert result["available"] is True
    assert result["total"] == 3
    assert result["events"][0]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"
    assert result["events"][1]["eventType"] == "EVENT_TYPE_ACTIVITY_TASK_FAILED"
    assert result["events"][2]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED"
    assert result["archived"] is False
    assert result["next_page_token"] == ""

    mock_client.get_workflow_history.assert_called_once_with(
        workflow_id="wf-1",
        run_id="run-1",
        next_page_token=None,
    )


def test_run_returns_error_on_failure(monkeypatch) -> None:
    tool = TemporalWorkflowHistoryTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get_workflow_history.return_value = {
        "success": False,
        "error": "HTTP 404: Workflow not found.",
    }

    monkeypatch.setattr(
        "tools.temporal_tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(
        base_url="http://localhost:7233",
        workflow_id="nonexistent-wf",
        namespace="default",
    )
    assert result["available"] is False
    assert "404" in result["error"]
    assert result["events"] == []
