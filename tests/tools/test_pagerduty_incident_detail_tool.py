"""Unit tests for PagerDutyIncidentDetailTool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from vendors.pagerduty import PagerDutyIncidentDetailTool


def _tool() -> PagerDutyIncidentDetailTool:
    return PagerDutyIncidentDetailTool()


def test_is_available_requires_connection_verified() -> None:
    assert _tool().is_available({"pagerduty": {"connection_verified": True}}) is True
    assert _tool().is_available({"pagerduty": {}}) is False
    assert _tool().is_available({}) is False


def test_extract_params_maps_source_fields() -> None:
    sources = {
        "pagerduty": {
            "api_key": "pd-key",
            "base_url": "https://api.pagerduty.com",
            "incident_id": "P123ABC",
        }
    }
    params = _tool().extract_params(sources)
    assert params["api_key"] == "pd-key"
    assert params["incident_id"] == "P123ABC"


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_returns_incident_and_log_entries(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_incident.return_value = {
        "success": True,
        "incident": {"id": "P1", "title": "CPU spike", "status": "triggered"},
    }
    mock_client.list_incident_log_entries.return_value = {
        "success": True,
        "log_entries": [
            {"id": "L1", "type": "trigger_log_entry", "summary": "Triggered via API"},
        ],
        "total": 1,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", incident_id="P1")
    assert result["available"] is True
    assert result["incident"]["title"] == "CPU spike"
    assert len(result["log_entries"]) == 1
    assert result["total_log_entries"] == 1


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_skips_log_entries_when_disabled(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_incident.return_value = {
        "success": True,
        "incident": {"id": "P1", "title": "test"},
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", incident_id="P1", include_log_entries=False)
    assert result["available"] is True
    assert result["log_entries"] == []
    mock_client.list_incident_log_entries.assert_not_called()


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_handles_incident_fetch_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_incident.return_value = {"success": False, "error": "HTTP 404: Not Found"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", incident_id="bad-id")
    assert result["available"] is False
    assert "404" in result["error"]


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_handles_log_entries_failure_gracefully(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_incident.return_value = {
        "success": True,
        "incident": {"id": "P1", "title": "test"},
    }
    mock_client.list_incident_log_entries.return_value = {
        "success": False,
        "error": "timeout",
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", incident_id="P1")
    assert result["available"] is True
    assert result["log_entries"] == []


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="", incident_id="P1")
    assert result["available"] is False


def test_run_returns_error_without_incident_id() -> None:
    result = _tool().run(api_key="k", incident_id="")
    assert result["available"] is False
    assert "incident_id is required" in result["error"]


def test_metadata_requires_incident_id() -> None:
    t = _tool()
    assert t.name == "pagerduty_incident_detail"
    assert "incident_id" in t.input_schema["required"]
