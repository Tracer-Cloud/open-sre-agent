"""Tests for the incident.io tool."""

from unittest.mock import MagicMock

import pytest

from app.tools.IncidentIoIncidentsTool import IncidentIoIncidentsTool


@pytest.fixture
def tool():
    return IncidentIoIncidentsTool()


def test_incident_io_tool_is_available(tool):
    """Test availability check."""
    assert tool.is_available({"incident_io": {"connection_verified": True}})
    assert not tool.is_available({"incident_io": {"connection_verified": False}})
    assert not tool.is_available({})


def test_incident_io_tool_extract_params(tool):
    """Test param extraction."""
    sources = {"incident_io": {"api_key": "test-key"}}
    params = tool.extract_params(sources)
    assert params["api_key"] == "test-key"


def test_incident_io_tool_run_list(tool, monkeypatch):
    """Test listing incidents."""
    mock_client = MagicMock()
    mock_client.list_incidents.return_value = {
        "success": True,
        "incidents": [{"id": "1"}],
        "total": 1,
    }

    mock_make_client = MagicMock(return_value=mock_client)
    monkeypatch.setattr(
        "app.tools.IncidentIoIncidentsTool.make_incident_io_client", mock_make_client
    )

    result = tool.run(api_key="test-key", action="list", status="live")

    mock_make_client.assert_called_once_with("test-key")
    mock_client.list_incidents.assert_called_once_with(status="live", page_size=None, after=None)
    assert result["success"] is True
    assert result["action"] == "list"
    assert result["total"] == 1


def test_incident_io_tool_run_add_timeline(tool, monkeypatch):
    """Test adding a timeline event."""
    mock_client = MagicMock()
    mock_client.add_timeline_event.return_value = {"success": True}

    mock_make_client = MagicMock(return_value=mock_client)
    monkeypatch.setattr(
        "app.tools.IncidentIoIncidentsTool.make_incident_io_client", mock_make_client
    )

    result = tool.run(
        api_key="test-key",
        action="add_timeline",
        incident_id="inc-123",
        title="Test Update",
        comment="This is a test.",
    )

    mock_client.add_timeline_event.assert_called_once_with(
        "inc-123", title="Test Update", description="This is a test."
    )
    assert result["success"] is True
    assert result["action"] == "add_timeline"


def test_incident_io_tool_run_get(tool, monkeypatch):
    """Test getting a specific incident."""
    mock_client = MagicMock()
    mock_client.get_incident.return_value = {"success": True, "incident": {"id": "inc-123"}}

    mock_make_client = MagicMock(return_value=mock_client)
    monkeypatch.setattr(
        "app.tools.IncidentIoIncidentsTool.make_incident_io_client", mock_make_client
    )

    result = tool.run(api_key="test-key", action="get", incident_id="inc-123")

    mock_client.get_incident.assert_called_once_with("inc-123")
    assert result["success"] is True
    assert result["action"] == "get"
    assert result["incident"]["id"] == "inc-123"


def test_incident_io_tool_run_missing_args(tool, monkeypatch):
    """Test errors when missing required args."""
    mock_client = MagicMock()
    mock_make_client = MagicMock(return_value=mock_client)
    monkeypatch.setattr(
        "app.tools.IncidentIoIncidentsTool.make_incident_io_client", mock_make_client
    )

    result = tool.run(api_key="test-key", action="add_timeline", incident_id="")
    assert result["success"] is False
    assert "incident_id is required" in result["error"]

    result = tool.run(api_key="test-key", action="add_timeline", incident_id="123", title="")
    assert result["success"] is False
    assert "title is required" in result["error"]

    result = tool.run(api_key="test-key", action="get", incident_id="")
    assert result["success"] is False
    assert "incident_id is required" in result["error"]
