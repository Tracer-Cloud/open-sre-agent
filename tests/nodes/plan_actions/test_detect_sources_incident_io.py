from app.nodes.plan_actions.detect_sources import (
    _extract_incident_io_id_from_url,
    detect_sources,
)


def test_extract_incident_io_id_from_url() -> None:
    assert (
        _extract_incident_io_id_from_url("https://app.incident.io/incidents/inc-123/timeline")
        == "inc-123"
    )
    assert _extract_incident_io_id_from_url("https://example.com/incidents/inc-123") == ""


def test_detect_sources_adds_incident_io_without_live_probe() -> None:
    resolved_integrations = {
        "incident_io": {
            "api_key": "secret",
            "region": "us",
            "base_url": "https://api.incident.io",
            "integration_id": "integration-1",
        }
    }
    raw_alert = {
        "annotations": {
            "incident_url": "https://app.incident.io/incidents/inc-123/timeline",
            "incident_io_status_category": "live",
        }
    }

    sources = detect_sources(raw_alert, {}, resolved_integrations)

    assert sources["incident_io"] == {
        "api_key": "secret",
        "base_url": "https://api.incident.io",
        "incident_id": "inc-123",
        "status_category": "live",
        "connection_verified": True,
        "integration_id": "integration-1",
    }
