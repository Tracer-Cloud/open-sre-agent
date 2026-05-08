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

    monkeypatch.setattr(httpx.Client, "request", lambda _self, *_, **__: mock_resp)

    res = client.get_incident("inc-123")
    assert res["success"] is True
    assert res["incident"]["summary"] == "Original summary content"


def test_add_timeline_event_preferred_endpoint(client, monkeypatch):
    """Verify add_timeline_event uses the atomic timeline endpoint if available."""
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 201
    mock_post_resp.raise_for_status.return_value = None

    calls = []

    def mock_request(_self, method, url, **kwargs):
        calls.append((method, url, kwargs))
        return mock_post_resp

    monkeypatch.setattr(httpx.Client, "request", mock_request)

    res = client.add_timeline_event("inc-123", title="RCA Finding", description="Root cause found.")

    assert res["success"] is True
    assert len(calls) == 1
    assert calls[0][1] == "/v2/incident_timeline_events"
    assert "RCA Finding" in calls[0][2]["json"]["content"]


def test_add_timeline_event_fallback_contract(client, monkeypatch):
    """Verify add_timeline_event falls back to summary-append if timeline API is 404."""
    # 1. Mock the 404 for the timeline API
    mock_timeline_resp = MagicMock()
    mock_timeline_resp.status_code = 404

    def raise_404():
        raise httpx.HTTPStatusError("404", request=MagicMock(), response=mock_timeline_resp)

    mock_timeline_resp.raise_for_status.side_effect = raise_404

    # 2. Mock the GET call for summary fallback
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = {"incident": {"id": "inc-123", "summary": "Old."}}

    # 3. Mock the POST call for edit action
    mock_edit_resp = MagicMock()
    mock_edit_resp.status_code = 200
    mock_edit_resp.raise_for_status.return_value = None

    calls = []

    def mock_request(_self, method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            return mock_get_resp
        if url == "/v2/incident_timeline_events":
            return mock_timeline_resp
        return mock_edit_resp

    monkeypatch.setattr(httpx.Client, "request", mock_request)

    res = client.add_timeline_event("inc-123", title="New", description="Desc")

    assert res["success"] is True
    # Should see: POST timeline (404) -> GET incident -> POST edit
    assert calls[0][1] == "/v2/incident_timeline_events"
    assert calls[1][0] == "GET"
    assert calls[2][1] == "/v2/incidents/inc-123/actions/edit"
    assert "Old." in calls[2][2]["json"]["incident"]["summary"]
    assert "New" in calls[2][2]["json"]["incident"]["summary"]


def test_add_timeline_event_failure_handling(client, monkeypatch):
    """Verify 404/401 failure handling in the write-back path."""
    mock_404_resp = MagicMock()
    mock_404_resp.status_code = 404
    mock_404_resp.text = '{"error": "Not Found"}'

    # Ensure raise_for_status behaves like real httpx
    def raise_err():
        raise httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=mock_404_resp)

    mock_404_resp.raise_for_status.side_effect = raise_err

    # Mock both timeline and edit attempts to fail with 404
    monkeypatch.setattr(httpx.Client, "request", lambda *_, **__: mock_404_resp)

    res = client.add_timeline_event("inc-123", title="Title")

    assert res["success"] is False
    assert "HTTP 404" in res["error"]


def test_list_incidents_retry_on_429(client, monkeypatch):
    """Verify list_incidents retries on 429 and eventually succeeds."""
    mock_429_resp = MagicMock()
    mock_429_resp.status_code = 429

    def raise_429():
        raise httpx.HTTPStatusError("429", request=MagicMock(), response=mock_429_resp)

    mock_429_resp.raise_for_status.side_effect = raise_429

    mock_200_resp = MagicMock()
    mock_200_resp.status_code = 200
    mock_200_resp.json.return_value = {"incidents": []}
    mock_200_resp.raise_for_status.return_value = None

    calls = []

    def mock_request(_self, method, url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return mock_429_resp
        return mock_200_resp

    monkeypatch.setattr(httpx.Client, "request", mock_request)
    # Mock time.sleep to avoid waiting in tests
    monkeypatch.setattr("time.sleep", lambda _: None)

    res = client.list_incidents()

    assert res["success"] is True
    assert len(calls) == 2
    assert calls[0] == "/v2/incidents"
    assert calls[1] == "/v2/incidents"
