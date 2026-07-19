"""Tests for GrafanaAlertRulesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from integrations.grafana.tools import query_grafana_alert_rules
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGrafanaAlertRulesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_grafana_alert_rules.__opensre_registered_tool__


def test_is_available_requires_grafana_creds() -> None:
    rt = query_grafana_alert_rules.__opensre_registered_tool__
    assert rt.is_available({"grafana": {"connection_verified": True}}) is True
    assert rt.is_available({"grafana": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_grafana_alert_rules.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["grafana_endpoint"] == "https://grafana.example.com"


def test_run_with_backend() -> None:
    mock_backend = MagicMock()
    mock_backend.query_alert_rules.return_value = {
        "groups": [
            {
                "name": "my-group",
                "rules": [
                    {
                        "name": "RDSFreeStorageSpaceLow",
                        "state": "firing",
                        "labels": {"service": "rds"},
                        "annotations": {"summary": "storage low"},
                    }
                ],
            }
        ]
    }
    result = query_grafana_alert_rules(grafana_backend=mock_backend)
    assert result["available"] is True
    assert "raw" in result
    assert result["total_rules"] == 1
    assert result["rules"][0]["rule_name"] == "RDSFreeStorageSpaceLow"
    assert result["rules"][0]["group"] == "my-group"


def test_run_no_client() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch("integrations.grafana.tools._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_alert_rules(grafana_endpoint="http://grafana")
    assert result["available"] is False


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_alert_rules.return_value = [
        {"uid": "r1", "title": "High CPU", "state": "Firing"}
    ]
    with patch("integrations.grafana.tools._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_alert_rules(grafana_endpoint="http://grafana")
    assert result["available"] is True
    assert result["total_rules"] == 1


def test_run_with_folder_filter() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_alert_rules.return_value = []
    with patch("integrations.grafana.tools._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_alert_rules(folder="my-folder", grafana_endpoint="http://grafana")
    assert result["folder_filter"] == "my-folder"
    mock_client.query_alert_rules.assert_called_once_with(folder="my-folder")


def test_run_http_error_reports_unavailable_not_empty() -> None:
    # Regression for #2944: an API failure must surface as available=False, not
    # as an empty "0 alert rules" result the agent would read as healthy.
    mock_client = MagicMock()
    mock_client.is_configured = True
    response = MagicMock(status_code=401)
    mock_client.query_alert_rules.side_effect = requests.HTTPError(response=response)
    with patch("integrations.grafana.tools._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_alert_rules(grafana_endpoint="http://grafana")
    assert result["available"] is False
    assert "401" in result["error"]
    assert result["rules"] == []


def test_run_connection_error_reports_unavailable() -> None:
    # A transport error carries no response; the message falls back to the type.
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_alert_rules.side_effect = requests.ConnectionError("dns failure")
    with patch("integrations.grafana.tools._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_alert_rules(grafana_endpoint="http://grafana")
    assert result["available"] is False
    assert "ConnectionError" in result["error"]
    assert result["rules"] == []


def test_run_api_failure_logs_warning(caplog) -> None:
    # The failure must leave a server-side trace (the base client used to log
    # before it stopped swallowing); the agent-facing envelope is not enough.
    import logging

    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_alert_rules.side_effect = requests.ConnectionError("dns failure")
    with (
        patch("integrations.grafana.tools._resolve_grafana_client", return_value=mock_client),
        caplog.at_level(logging.WARNING, logger="integrations.grafana.tools"),
    ):
        query_grafana_alert_rules(grafana_endpoint="http://grafana")
    assert any("alert-rules query failed" in r.message for r in caplog.records)
