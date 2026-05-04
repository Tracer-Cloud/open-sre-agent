"""End-to-end tests for the incident.io integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.incident_io.client import make_incident_io_client
from app.tools.IncidentIoIncidentsTool import IncidentIoIncidentsTool


def test_incident_io_e2e_investigation_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate an agent using the IncidentIoIncidentsTool to list and update incidents."""
    client = make_incident_io_client("e2e-test-key")
    assert client is not None

    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append((method, str(url)))
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        if method == "GET" and "/v2/incidents" in str(url) and "/timeline_entries" not in str(url):
            if str(url).endswith("/v2/incidents/inc-123"):
                mock_resp.json.return_value = {
                    "incident": {
                        "id": "inc-123",
                        "name": "Database Outage",
                        "status": "open",
                    }
                }
            else:
                mock_resp.json.return_value = {
                    "incidents": [
                        {
                            "id": "inc-123",
                            "name": "Database Outage",
                            "status": "open",
                        }
                    ]
                }
            return mock_resp

        if method == "POST" and "/timeline_entries" in str(url):
            mock_resp.json.return_value = {}
            return mock_resp

        raise AssertionError(f"Unexpected request: {method} {url}")

    # Mock the internal client's send/request methods
    mock_httpx_client = MagicMock()
    mock_httpx_client.get = lambda url, **kwargs: fake_request("GET", url, **kwargs)
    mock_httpx_client.post = lambda url, **kwargs: fake_request("POST", url, **kwargs)

    # We patch _get_client so the IncidentIoClient uses our mock
    monkeypatch.setattr(
        "app.services.incident_io.client.IncidentIoClient._get_client",
        lambda _self: mock_httpx_client,
    )

    tool = IncidentIoIncidentsTool()

    # Step 1: Agent lists open incidents
    list_result = tool.run(api_key="e2e-test-key", action="list", status="open")
    assert list_result["success"] is True
    assert len(list_result["incidents"]) == 1
    assert list_result["incidents"][0]["id"] == "inc-123"

    # Step 2: Agent gets details for the incident
    get_result = tool.run(api_key="e2e-test-key", action="get", incident_id="inc-123")
    assert get_result["success"] is True
    assert get_result["incident"]["id"] == "inc-123"

    # Step 3: Agent posts a timeline update with findings
    update_result = tool.run(
        api_key="e2e-test-key",
        action="add_timeline",
        incident_id="inc-123",
        title="RCA Update",
        comment="Found the root cause in the DB replication lag.",
    )
    assert update_result["success"] is True

    assert calls == [
        ("GET", "/v2/incidents"),
        ("GET", "/v2/incidents/inc-123"),
        ("POST", "/v2/incidents/inc-123/timeline_entries"),
    ]
