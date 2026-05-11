from __future__ import annotations

from unittest.mock import MagicMock

from app.tools.IncidentIoIncidentsTool import IncidentIoIncidentsTool


def test_incident_io_tool_extracts_credentials_from_sources() -> None:
    tool = IncidentIoIncidentsTool()

    params = tool.extract_params(
        {
            "incident_io": {
                "api_key": "secret",
                "base_url": "https://api.incident.io",
                "incident_id": "inc-123",
            }
        }
    )

    assert params["api_key"] == "secret"
    assert params["action"] == "context"
    assert params["incident_id"] == "inc-123"
    assert tool.input_schema["required"] == []


def test_incident_io_tool_runs_context(monkeypatch) -> None:
    tool = IncidentIoIncidentsTool()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.get_incident_context.return_value = {
        "success": True,
        "incident": {"id": "inc-123"},
        "incident_updates": [],
    }

    monkeypatch.setattr(
        "app.tools.IncidentIoIncidentsTool.make_incident_io_client",
        lambda *_args, **_kwargs: client,
    )

    result = tool.run(api_key="secret", action="context", incident_id="inc-123")

    assert result["success"] is True
    assert result["source"] == "incident_io"
    client.get_incident_context.assert_called_once_with("inc-123", update_limit=20)


def test_incident_io_tool_runs_append_summary(monkeypatch) -> None:
    tool = IncidentIoIncidentsTool()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.append_summary_update.return_value = {"success": True}

    monkeypatch.setattr(
        "app.tools.IncidentIoIncidentsTool.make_incident_io_client",
        lambda *_args, **_kwargs: client,
    )

    result = tool.run(
        api_key="secret",
        action="append_summary",
        incident_id="inc-123",
        title="RCA",
        body="Finding",
        notify_incident_channel=True,
    )

    assert result["success"] is True
    client.append_summary_update.assert_called_once_with(
        "inc-123",
        title="RCA",
        body="Finding",
        notify_incident_channel=True,
    )


def test_incident_io_tool_requires_incident_id_for_context(monkeypatch) -> None:
    tool = IncidentIoIncidentsTool()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None

    monkeypatch.setattr(
        "app.tools.IncidentIoIncidentsTool.make_incident_io_client",
        lambda *_args, **_kwargs: client,
    )

    result = tool.run(api_key="secret", action="context")

    assert result["success"] is False
    assert "incident_id" in result["error"]
