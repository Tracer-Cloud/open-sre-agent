"""Tests for TemporalTaskQueueTool."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.tools.conftest import BaseToolContract, mock_agent_state
from tools.temporal_tools import TemporalTaskQueueTool


class TestTemporalTaskQueueToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return TemporalTaskQueueTool()


def test_is_available_when_configured() -> None:
    tool = TemporalTaskQueueTool()
    assert tool.is_available({"temporal": {"base_url": "http://localhost:7233"}}) is True


def test_is_available_when_not_configured() -> None:
    tool = TemporalTaskQueueTool()
    assert tool.is_available({"temporal": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = TemporalTaskQueueTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["base_url"] == "http://localhost:7233"
    assert params["namespace"] == "default"
    assert params["api_key"] == ""


def test_run_returns_unavailable_when_no_base_url() -> None:
    tool = TemporalTaskQueueTool()
    result = tool.run(base_url="", task_queue_name="my-queue")
    assert result["available"] is False
    assert "base_url is required" in result["error"]
    assert result["pollers"] == []
    assert result["stats"] == {}


def test_run_returns_error_when_no_task_queue_name() -> None:
    tool = TemporalTaskQueueTool()
    result = tool.run(base_url="http://localhost:7233", task_queue_name="")
    assert result["available"] is True
    assert "task_queue_name is required" in result["error"]
    assert result["pollers"] == []


def test_run_happy_path(monkeypatch) -> None:
    tool = TemporalTaskQueueTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.describe_task_queue.return_value = {
        "success": True,
        "pollers": [
            {
                "lastAccessTime": "2024-01-15T10:05:00Z",
                "identity": "worker-1@host-abc",
                "ratePerSecond": 100.0,
            },
            {
                "lastAccessTime": "2024-01-15T10:04:55Z",
                "identity": "worker-2@host-def",
                "ratePerSecond": 100.0,
            },
        ],
        "stats": {
            "approximateBacklogCount": "42",
            "approximateBacklogAge": "30.5s",
            "tasksAddRate": 5.2,
            "tasksDispatchRate": 4.8,
        },
        "total": 2,
    }

    monkeypatch.setattr(
        "tools.temporal_tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(
        base_url="http://localhost:7233",
        task_queue_name="payment-queue",
        namespace="default",
    )
    assert result["available"] is True
    assert result["total"] == 2
    assert result["pollers"][0]["identity"] == "worker-1@host-abc"
    assert result["stats"]["approximateBacklogCount"] == "42"
    assert result["stats"]["tasksAddRate"] == 5.2

    mock_client.describe_task_queue.assert_called_once_with(task_queue_name="payment-queue")


def test_run_returns_error_on_failure(monkeypatch) -> None:
    tool = TemporalTaskQueueTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.describe_task_queue.return_value = {
        "success": False,
        "error": "HTTP 404: Task queue not found.",
    }

    monkeypatch.setattr(
        "tools.temporal_tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(
        base_url="http://localhost:7233",
        task_queue_name="nonexistent-queue",
        namespace="default",
    )
    assert result["available"] is False
    assert "404" in result["error"]
    assert result["pollers"] == []
    assert result["stats"] == {}
