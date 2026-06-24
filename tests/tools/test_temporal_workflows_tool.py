"""Tests for TemporalWorkflowsTool."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.tools.TemporalWorkflowsTool import TemporalWorkflowsTool
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTemporalWorkflowsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return TemporalWorkflowsTool()


def test_is_available_when_configured() -> None:
    tool = TemporalWorkflowsTool()
    assert tool.is_available({"temporal": {"base_url": "http://localhost:7233"}}) is True


def test_is_available_when_not_configured() -> None:
    tool = TemporalWorkflowsTool()
    assert tool.is_available({"temporal": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = TemporalWorkflowsTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["base_url"] == "http://localhost:7233"
    assert params["namespace"] == "default"
    assert params["api_key"] == ""


def test_run_returns_unavailable_when_no_base_url() -> None:
    tool = TemporalWorkflowsTool()
    result = tool.run(base_url="")
    assert result["available"] is False
    assert "base_url is required" in result["error"]
    assert result["executions"] == []


def test_run_happy_path(monkeypatch) -> None:
    tool = TemporalWorkflowsTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.list_workflow_executions.return_value = {
        "success": True,
        "executions": [
            {
                "execution": {"workflowId": "wf-1", "runId": "run-1"},
                "type": {"name": "PaymentWorkflow"},
                "startTime": "2024-01-15T10:00:00Z",
                "closeTime": "2024-01-15T10:05:00Z",
                "status": "WORKFLOW_EXECUTION_STATUS_FAILED",
                "taskQueue": "payment-queue",
                "historyLength": "42",
                "historySizeBytes": "8192",
            }
        ],
        "next_page_token": "",
        "total": 1,
    }

    monkeypatch.setattr(
        "app.tools.TemporalWorkflowsTool.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(base_url="http://localhost:7233", namespace="default")
    assert result["available"] is True
    assert result["total"] == 1
    assert result["executions"][0]["status"] == "WORKFLOW_EXECUTION_STATUS_FAILED"
    assert result["executions"][0]["taskQueue"] == "payment-queue"
    assert result["next_page_token"] == ""


def test_run_returns_error_on_failure(monkeypatch) -> None:
    tool = TemporalWorkflowsTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.list_workflow_executions.return_value = {
        "success": False,
        "error": "HTTP 401: Unauthorized",
    }

    monkeypatch.setattr(
        "app.tools.TemporalWorkflowsTool.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(base_url="http://localhost:7233", namespace="default")
    assert result["available"] is False
    assert "401" in result["error"]
    assert result["executions"] == []


def test_run_passes_pagination_token(monkeypatch) -> None:
    tool = TemporalWorkflowsTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.list_workflow_executions.return_value = {
        "success": True,
        "executions": [],
        "next_page_token": "",
        "total": 0,
    }

    monkeypatch.setattr(
        "app.tools.TemporalWorkflowsTool.TemporalClient",
        lambda _config: mock_client,
    )

    tool.run(base_url="http://localhost:7233", namespace="default", next_page_token="abc123")
    mock_client.list_workflow_executions.assert_called_once_with(next_page_token="abc123")
