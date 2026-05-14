from __future__ import annotations

from unittest.mock import MagicMock

from app.tools.ServiceNowRecordsTool import ServiceNowRecordsTool


def test_servicenow_tool_extracts_credentials_from_sources() -> None:
    tool = ServiceNowRecordsTool()

    params = tool.extract_params(
        {
            "servicenow": {
                "instance_url": "https://dev12345.service-now.com",
                "api_token": "token",
                "incident_id": "INC001",
            }
        }
    )

    assert params["instance_url"] == "https://dev12345.service-now.com"
    assert params["api_token"] == "token"
    assert params["action"] == "context"
    assert params["incident_id"] == "INC001"


def test_servicenow_tool_runs_context(monkeypatch) -> None:
    tool = ServiceNowRecordsTool()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.get_context.return_value = {
        "success": True,
        "incident": {"number": "INC001"},
        "changes": [],
        "services": [],
    }

    monkeypatch.setattr(
        "app.tools.ServiceNowRecordsTool.make_servicenow_client",
        lambda *_args, **_kwargs: client,
    )

    result = tool.run(
        instance_url="https://dev12345.service-now.com",
        api_token="token",
        action="context",
        incident_id="INC001",
    )

    assert result["success"] is True
    assert result["source"] == "servicenow"
    client.get_context.assert_called_once_with(
        "INC001",
        change_query="active=true^ORDERBYDESCsys_updated_on",
        service_query="",
        limit=10,
    )


def test_servicenow_tool_runs_append_work_note(monkeypatch) -> None:
    tool = ServiceNowRecordsTool()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.append_work_note.return_value = {"success": True}

    monkeypatch.setattr(
        "app.tools.ServiceNowRecordsTool.make_servicenow_client",
        lambda *_args, **_kwargs: client,
    )

    result = tool.run(
        instance_url="https://dev12345.service-now.com",
        api_token="token",
        action="append_work_note",
        incident_id="INC001",
        note="Finding",
    )

    assert result["success"] is True
    client.append_work_note.assert_called_once_with("INC001", "Finding")
