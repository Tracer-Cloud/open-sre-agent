from unittest.mock import patch

from app.integrations.probes import ProbeResult
from app.nodes.plan_actions.detect_sources import _probe_incident_io_cached, detect_sources


def test_detect_sources_incident_io_connection_verified_success():
    """Test that connection_verified is True when the probe passes."""
    resolved_integrations = {
        "incident_io": {
            "api_key": "test-key",
            "region": "us",
            "base_url": "https://api.incident.io",
        }
    }
    raw_alert = {"annotations": {"incident_id": "INC-123"}}

    # Mock the probe to pass
    with patch("app.nodes.plan_actions.detect_sources._probe_incident_io_cached") as mock_probe:
        mock_probe.return_value = True

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert sources["incident_io"]["connection_verified"] is True
        mock_probe.assert_called_once_with("test-key", "us", "https://api.incident.io")


def test_detect_sources_incident_io_connection_verified_failure():
    """Test that connection_verified is False when the probe fails."""
    resolved_integrations = {"incident_io": {"api_key": "bad-key", "region": "us"}}
    raw_alert = {"annotations": {"incident_id": "INC-123"}}

    # Mock the probe to fail
    with patch("app.nodes.plan_actions.detect_sources._probe_incident_io_cached") as mock_probe:
        mock_probe.return_value = False

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert sources["incident_io"]["connection_verified"] is False


def test_detect_sources_incident_io_probe_caching():
    """Test that the probe result is cached by lru_cache."""
    resolved_integrations = {
        "incident_io": {
            "api_key": "test-key",
            "region": "us",
            "base_url": "https://api.incident.io",
        }
    }
    raw_alert = {"annotations": {"incident_id": "INC-123"}}

    # Clear the cache first
    _probe_incident_io_cached.cache_clear()

    with patch(
        "app.services.incident_io.client.IncidentIoClient.probe_access"
    ) as mock_probe_access:
        mock_probe_access.return_value = ProbeResult.passed("ok")

        # Call detect_sources multiple times
        detect_sources(raw_alert, {}, resolved_integrations)
        detect_sources(raw_alert, {}, resolved_integrations)
        detect_sources(raw_alert, {}, resolved_integrations)

        # probe_access should only be called once due to lru_cache
        assert mock_probe_access.call_count == 1
