"""Unit tests for PagerDutyIncidentsTool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.pagerduty_tools import PagerDutyIncidentsTool


def _tool() -> PagerDutyIncidentsTool:
    return PagerDutyIncidentsTool()


def test_is_available_with_connection_verified() -> None:
    assert _tool().is_available({"pagerduty": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified() -> None:
    assert _tool().is_available({"pagerduty": {}}) is False
    assert _tool().is_available({}) is False


def test_extract_params_maps_source_fields() -> None:
    sources = {
        "pagerduty": {
            "api_key": "pd-key",
            "base_url": "https://api.pagerduty.com",
        }
    }
    params = _tool().extract_params(sources)
    assert params["api_key"] == "pd-key"
    assert params["base_url"] == "https://api.pagerduty.com"


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_returns_incidents_and_active_subset(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_incidents.return_value = {
        "success": True,
        "incidents": [
            {"id": "P1", "status": "triggered", "urgency": "high"},
            {"id": "P2", "status": "acknowledged", "urgency": "high"},
            {"id": "P3", "status": "resolved", "urgency": "low"},
        ],
        "total": 3,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["total"] == 3
    assert len(result["active_incidents"]) == 2
    assert result["active_incidents"][0]["id"] == "P1"
    assert result["active_incidents"][1]["id"] == "P2"


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_empty_incidents_list(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_incidents.return_value = {"success": True, "incidents": [], "total": 0}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["incidents"] == []
    assert result["active_incidents"] == []


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_returns_unavailable_on_api_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_incidents.return_value = {"success": False, "error": "HTTP 401: Unauthorized"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is False
    assert "401" in result["error"]


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="")
    assert result["available"] is False


@patch("tools.pagerduty_tools.make_pagerduty_client")
def test_run_passes_filter_params(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_incidents.return_value = {"success": True, "incidents": [], "total": 0}
    mock_make.return_value = mock_client

    _tool().run(
        api_key="k",
        statuses=["triggered"],
        urgencies=["high"],
        service_ids=["SVC1"],
        since="2024-01-01T00:00:00Z",
        until="2024-01-02T00:00:00Z",
        limit=10,
    )
    mock_client.list_incidents.assert_called_once_with(
        statuses=["triggered"],
        urgencies=["high"],
        service_ids=["SVC1"],
        since="2024-01-01T00:00:00Z",
        until="2024-01-02T00:00:00Z",
        limit=10,
    )


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "pagerduty_incidents"
    assert t.source == "pagerduty"
    assert "api_key" in t.input_schema["required"]
