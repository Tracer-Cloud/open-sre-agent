from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.integrations.config_models import ServiceNowIntegrationConfig
from app.services.servicenow import ServiceNowClient, make_servicenow_client


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    response.status_code = 200
    return response


@pytest.fixture
def client() -> ServiceNowClient:
    return ServiceNowClient(
        ServiceNowIntegrationConfig(
            instance_url="https://dev12345.service-now.com",
            username="admin",
            password="secret",
        )
    )


def test_config_rejects_non_https_remote_instance_url() -> None:
    with pytest.raises(ValueError, match="must use https"):
        ServiceNowIntegrationConfig(
            instance_url="http://169.254.169.254/latest",
            username="admin",
            password="secret",
        )


def test_config_requires_token_or_basic_auth() -> None:
    with pytest.raises(ValueError, match="requires api_token"):
        ServiceNowIntegrationConfig(instance_url="https://dev12345.service-now.com")


def test_make_client_allows_bearer_token() -> None:
    created = make_servicenow_client(
        "https://dev12345.service-now.com",
        api_token="token",
    )

    assert created is not None
    assert created.config.headers["Authorization"] == "Bearer token"


def test_client_prefers_basic_auth_when_basic_and_token_are_present(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr("app.services.servicenow.client.httpx.Client", fake_client)

    ServiceNowClient(
        ServiceNowIntegrationConfig(
            instance_url="https://dev12345.service-now.com",
            username="admin",
            password="secret",
            api_token="stale-token",
        )
    )

    assert captured["auth"] == ("admin", "secret")
    assert "Authorization" not in captured["headers"]


def test_list_incidents_uses_table_api_query(client: ServiceNowClient, monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_rows(table: str, **kwargs):
        calls.append((table, kwargs))
        return [
            {
                "sys_id": "abc",
                "number": "INC001",
                "short_description": "Checkout down",
                "assignment_group": {"display_value": "SRE", "value": "grp"},
            }
        ]

    monkeypatch.setattr(client, "_rows", fake_rows)

    result = client.list_incidents(query="active=true", limit=1)

    assert result["success"] is True
    assert result["incidents"][0]["assignment_group"] == "SRE"
    assert calls[0][0] == "incident"
    assert calls[0][1]["query"] == "active=true"
    assert calls[0][1]["limit"] == 1


def test_context_reads_incident_changes_and_services(
    client: ServiceNowClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        client,
        "get_incident",
        lambda incident_id: {"success": True, "incident": {"number": incident_id}},
    )
    monkeypatch.setattr(
        client,
        "list_recent_changes",
        lambda **_kwargs: {"success": True, "changes": [{"number": "CHG001"}]},
    )
    monkeypatch.setattr(
        client,
        "list_services",
        lambda **_kwargs: {"success": True, "services": [{"name": "checkout"}]},
    )

    result = client.get_context("INC001")

    assert result["success"] is True
    assert result["incident"]["number"] == "INC001"
    assert result["changes"][0]["number"] == "CHG001"
    assert result["services"][0]["name"] == "checkout"


def test_append_work_note_resolves_incident_number_to_sys_id(
    client: ServiceNowClient,
    monkeypatch,
) -> None:
    patch = MagicMock(return_value=_response({"result": {"sys_id": "abc", "number": "INC001"}}))
    monkeypatch.setattr(client, "get_incident", lambda _id: {"incident": {"sys_id": "abc"}})
    monkeypatch.setattr(client._client, "patch", patch)

    result = client.append_work_note("INC001", "OpenSRE finding")

    assert result["success"] is True
    patch.assert_called_once()
    assert patch.call_args.kwargs["json"] == {"work_notes": "OpenSRE finding"}
