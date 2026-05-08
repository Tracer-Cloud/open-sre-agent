"""Contract tests for IncidentIoClient ensuring the write-back uses the correct v2 endpoints."""

from unittest.mock import MagicMock

import httpx
import pytest

from app.services.incident_io.client import make_incident_io_client


@pytest.fixture
def client():
    return make_incident_io_client("test-key", "us")


def test_get_incident_includes_summary(client, monkeypatch):
    """Verify get_incident correctly extracts the summary field."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "incident": {
            "id": "inc-123",
            "summary": "Original summary content",
            "name": "Test Incident",
        }
    }
    mock_resp.raise_for_status.return_value = None

    monkeypatch.setattr(httpx.Client, "get", lambda *_, **__: mock_resp)

    res = client.get_incident("inc-123")
    assert res["success"] is True
    assert res["incident"]["summary"] == "Original summary content"


def test_add_timeline_event_contract(client, monkeypatch):
    """Verify add_timeline_event appends to summary via the actions/edit endpoint."""
    # 1. Mock the GET call to fetch existing summary
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = {
        "incident": {
            "id": "inc-123",
            "summary": "Existing summary.",
        }
    }

    # 2. Mock the POST call to edit the incident
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.raise_for_status.return_value = None

    # Track calls to verify the payload contract
    calls = []

    def mock_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            return mock_get_resp
        return mock_post_resp

    monkeypatch.setattr(httpx.Client, "request", mock_request)
    # httpx.Client.get/post call request internally in recent versions,
    # but we'll mock them directly to be safe if needed.
    monkeypatch.setattr(
        httpx.Client, "get", lambda _self, url, **kwargs: mock_request("GET", url, **kwargs)
    )
    monkeypatch.setattr(
        httpx.Client, "post", lambda _self, url, **kwargs: mock_request("POST", url, **kwargs)
    )

    res = client.add_timeline_event("inc-123", title="RCA Finding", description="Root cause found.")

    assert res["success"] is True

    # Verify POST call to the correct endpoint
    post_calls = [c for c in calls if c[0] == "POST"]
    assert len(post_calls) == 1
    method, url, kwargs = post_calls[0]

    assert url == "/v2/incidents/inc-123/actions/edit"
    payload = kwargs["json"]

    # Verify contract: incident.summary should contain both old and new content
    assert "Existing summary." in payload["incident"]["summary"]
    assert "OpenSRE Finding: RCA Finding" in payload["incident"]["summary"]
    assert "Root cause found." in payload["incident"]["summary"]
    assert payload["notify_incident_channel"] is False


def test_add_timeline_event_failure_handling(client, monkeypatch):
    """Verify 404/401 failure handling in the write-back path."""
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = {"incident": {"summary": "..."}}

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 404
    mock_post_resp.text = '{"error": "Not Found"}'

    # Ensure raise_for_status behaves like real httpx
    def raise_err():
        raise httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=mock_post_resp)

    mock_post_resp.raise_for_status.side_effect = raise_err

    monkeypatch.setattr(httpx.Client, "get", lambda *_, **__: mock_get_resp)
    monkeypatch.setattr(httpx.Client, "post", lambda *_, **__: mock_post_resp)

    res = client.add_timeline_event("inc-123", title="Title")

    assert res["success"] is False
    assert "HTTP 404" in res["error"]
