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


def test_extract_incident_io_id_rejects_lookalike_host() -> None:
    # "evilincident.io" ends with "incident.io" but is not a valid incident.io host.
    assert _extract_incident_io_id_from_url("https://evilincident.io/incidents/inc-999") == ""
    assert _extract_incident_io_id_from_url("https://notincident.io/incidents/inc-999") == ""
    # Subdomain of incident.io should still work.
    assert (
        _extract_incident_io_id_from_url("https://app.incident.io/incidents/inc-456") == "inc-456"
    )


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


def test_detect_sources_bare_incident_id_annotation_does_not_bleed_into_incident_io() -> None:
    """A bare 'incident_id' annotation (e.g. from PagerDuty) must not be forwarded to incident.io."""
    resolved_integrations = {
        "incident_io": {
            "api_key": "secret",
            "base_url": "https://api.incident.io",
            "integration_id": "integration-1",
        }
    }
    # Simulate a PagerDuty-originated alert that carries a generic incident_id annotation.
    raw_alert = {
        "alert_source": "pagerduty",
        "annotations": {
            "incident_id": "pagerduty-inc-999",
        },
    }

    sources = detect_sources(raw_alert, {}, resolved_integrations)

    # incident.io source is still registered (integration is configured)
    assert "incident_io" in sources
    # but the PagerDuty incident ID must NOT be forwarded
    assert sources["incident_io"]["incident_id"] == ""
