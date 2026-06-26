"""Unit tests for PagerDutyServicesTool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from vendors.pagerduty import PagerDutyServicesTool


def _tool() -> PagerDutyServicesTool:
    return PagerDutyServicesTool()


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
    assert params["service_id"] == ""


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_lists_services(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_services.return_value = {
        "success": True,
        "services": [
            {
                "id": "SVC1",
                "name": "Web App",
                "status": "active",
                "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
                "teams": [],
                "alert_creation": "create_alerts_and_incidents",
                "integrations": [{"id": "I1", "name": "Datadog", "type": "generic_events_api"}],
                "html_url": "https://app.pagerduty.com/services/SVC1",
            },
        ],
        "total": 1,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["total"] == 1
    assert result["services"][0]["name"] == "Web App"
    assert result["service"] == {}


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_gets_service_detail_when_id_provided(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_service.return_value = {
        "success": True,
        "service": {
            "id": "SVC1",
            "name": "Web App",
            "description": "Main web application",
            "status": "active",
            "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
            "teams": [],
            "alert_creation": "create_alerts_and_incidents",
            "incident_urgency_rule": {"type": "constant", "urgency": "high"},
            "integrations": [],
            "html_url": "https://app.pagerduty.com/services/SVC1",
        },
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", service_id="SVC1")
    assert result["available"] is True
    assert result["service"]["name"] == "Web App"
    assert result["service"]["incident_urgency_rule"]["urgency"] == "high"
    assert result["services"] == []
    assert result["total"] == 1


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_empty_services_list(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_services.return_value = {"success": True, "services": [], "total": 0}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["services"] == []
    assert result["total"] == 0


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_returns_unavailable_on_list_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_services.return_value = {"success": False, "error": "HTTP 401: Unauthorized"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is False
    assert "401" in result["error"]


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_returns_unavailable_on_detail_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_service.return_value = {"success": False, "error": "HTTP 404: Not Found"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", service_id="bad-id")
    assert result["available"] is False
    assert "404" in result["error"]


@patch("vendors.pagerduty.make_pagerduty_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="")
    assert result["available"] is False


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "pagerduty_services"
    assert t.source == "pagerduty"
    assert "api_key" in t.input_schema["required"]
