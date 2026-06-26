"""Tests for TemporalNamespaceInfoTool."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.tools.conftest import BaseToolContract, mock_agent_state
from tools.TemporalNamespaceInfoTool import TemporalNamespaceInfoTool


class TestTemporalNamespaceInfoToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return TemporalNamespaceInfoTool()


def test_is_available_when_configured() -> None:
    tool = TemporalNamespaceInfoTool()
    assert tool.is_available({"temporal": {"base_url": "http://localhost:7233"}}) is True


def test_is_available_when_not_configured() -> None:
    tool = TemporalNamespaceInfoTool()
    assert tool.is_available({"temporal": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = TemporalNamespaceInfoTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["base_url"] == "http://localhost:7233"
    assert params["namespace"] == "default"
    assert params["api_key"] == ""


def test_run_returns_unavailable_when_no_base_url() -> None:
    tool = TemporalNamespaceInfoTool()
    result = tool.run(base_url="")
    assert result["available"] is False
    assert "base_url is required" in result["error"]


def test_run_happy_path(monkeypatch) -> None:
    tool = TemporalNamespaceInfoTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get_namespace_info.return_value = {
        "success": True,
        "name": "default",
        "state": "NAMESPACE_STATE_REGISTERED",
        "workflow_count": "58",
        # The client flattens + base64-decodes the raw groupValues into
        # [{"status", "count"}] before returning (see TemporalClient).
        "groups": [
            {"status": "Running", "count": "45"},
            {"status": "Failed", "count": "8"},
            {"status": "TimedOut", "count": "5"},
        ],
    }

    monkeypatch.setattr(
        "tools.TemporalNamespaceInfoTool.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(base_url="http://localhost:7233", namespace="default")
    assert result["available"] is True
    assert result["name"] == "default"
    assert result["state"] == "NAMESPACE_STATE_REGISTERED"
    assert result["workflow_count"] == "58"
    assert result["groups"] == [
        {"status": "Running", "count": "45"},
        {"status": "Failed", "count": "8"},
        {"status": "TimedOut", "count": "5"},
    ]


def test_run_returns_error_on_failure(monkeypatch) -> None:
    tool = TemporalNamespaceInfoTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get_namespace_info.return_value = {
        "success": False,
        "error": "HTTP 404: Namespace not found.",
    }

    monkeypatch.setattr(
        "tools.TemporalNamespaceInfoTool.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(base_url="http://localhost:7233", namespace="bad-ns")
    assert result["available"] is False
    assert "404" in result["error"]
