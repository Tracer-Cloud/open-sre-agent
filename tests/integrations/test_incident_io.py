"""Tests for the incident.io integration configuration and client."""

from unittest.mock import MagicMock

import httpx

from app.integrations.config_models import IncidentIoIntegrationConfig
from app.services.incident_io.client import make_incident_io_client


def test_incident_io_config_normalization():
    """Test incident.io integration config parses correctly."""
    config = IncidentIoIntegrationConfig.model_validate({"api_key": "test-key"})
    assert config.api_key == "test-key"
    assert config.base_url == "https://api.incident.io"
    assert config.headers["Authorization"] == "Bearer test-key"
    assert config.headers["Content-Type"] == "application/json"


def test_incident_io_client_probe_success(monkeypatch):
    """Test that probe_access returns a passed result when the API list call succeeds."""
    client = make_incident_io_client("test-key")

    # Mock httpx.Client.request
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"incidents": []}
    mock_response.raise_for_status.return_value = None

    monkeypatch.setattr(httpx.Client, "request", lambda _self, *_, **__: mock_response)

    result = client.probe_access()
    assert result.status == "passed"
    assert "Connected to incident.io" in result.detail


def test_incident_io_client_probe_failure(monkeypatch):
    """Test that probe_access returns failed when the API list call raises an error."""
    client = make_incident_io_client("test-key")

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Unauthorized",
        request=MagicMock(),
        response=mock_response,
    )

    monkeypatch.setattr(httpx.Client, "request", lambda _self, *_, **__: mock_response)

    result = client.probe_access()
    assert result.status == "failed"
    assert "Connection failed" in result.detail


def test_make_incident_io_client_empty():
    """Test that factory function returns None for empty API key."""
    assert make_incident_io_client(None) is None
    assert make_incident_io_client("") is None
    assert make_incident_io_client("   ") is None
