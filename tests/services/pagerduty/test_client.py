"""Unit tests for PagerDuty REST API client."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.pagerduty.client import (
    PagerDutyClient,
    PagerDutyConfig,
    make_pagerduty_client,
)


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)[:200]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return self._payload


def _client() -> PagerDutyClient:
    return PagerDutyClient(PagerDutyConfig(api_key="test-pd-key"))


# --- Config ---


def test_is_configured_with_key() -> None:
    assert _client().is_configured is True


def test_is_configured_without_key() -> None:
    c = PagerDutyClient(PagerDutyConfig(api_key=""))
    assert c.is_configured is False


def test_default_base_url() -> None:
    c = _client()
    assert c.config.base_url == "https://api.pagerduty.com"


def test_custom_base_url() -> None:
    c = PagerDutyClient(PagerDutyConfig(api_key="k", base_url="https://custom.pd.com"))
    assert c.config.base_url == "https://custom.pd.com"


def test_headers_include_token() -> None:
    c = _client()
    assert c.config.headers["Authorization"] == "Token token=test-pd-key"
    assert c.config.headers["Content-Type"] == "application/json"


def test_probe_access_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"incidents": []}),
    )
    result = _client().probe_access()
    assert result.status == "passed"
    assert "PagerDuty" in result.detail


def test_probe_access_missing_key() -> None:
    c = PagerDutyClient(PagerDutyConfig(api_key=""))
    result = c.probe_access()
    assert result.status == "missing"


def test_probe_access_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"message": "Not Found", "code": 2100}, 401),
    )
    result = _client().probe_access()
    assert result.status == "failed"
    assert "401" in result.detail


# --- list_incidents ---


def test_list_incidents_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "incidents": [
            {
                "id": "P1ABC",
                "incident_number": 42,
                "title": "CPU spike on prod",
                "status": "triggered",
                "urgency": "high",
                "priority": {"id": "PR1", "name": "P1", "summary": "Critical"},
                "service": {"id": "SVC1", "summary": "Web", "type": "service_reference"},
                "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
                "assignments": [
                    {"assignee": {"id": "U1", "summary": "Alice", "type": "user_reference"}}
                ],
                "created_at": "2024-01-01T00:00:00Z",
                "last_status_change_at": "2024-01-01T00:05:00Z",
                "html_url": "https://app.pagerduty.com/incidents/P1ABC",
            },
        ]
    }
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().list_incidents()
    assert result["success"] is True
    assert result["total"] == 1
    assert result["incidents"][0]["title"] == "CPU spike on prod"
    assert result["incidents"][0]["priority"]["name"] == "P1"
    assert result["incidents"][0]["service"]["summary"] == "Web"


def test_list_incidents_with_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"incidents": []})

    monkeypatch.setattr("app.services.pagerduty.client.httpx.Client.get", _fake_get)
    _client().list_incidents(
        statuses=["triggered", "acknowledged"],
        urgencies=["high"],
        service_ids=["SVC1"],
        since="2024-01-01T00:00:00Z",
        until="2024-01-02T00:00:00Z",
        limit=10,
    )
    assert captured["params"]["statuses[]"] == ["triggered", "acknowledged"]
    assert captured["params"]["urgencies[]"] == ["high"]
    assert captured["params"]["service_ids[]"] == ["SVC1"]
    assert captured["params"]["since"] == "2024-01-01T00:00:00Z"
    assert captured["params"]["until"] == "2024-01-02T00:00:00Z"
    assert captured["params"]["limit"] == 10


def test_list_incidents_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "unauthorized"}, 401),
    )
    result = _client().list_incidents()
    assert result["success"] is False
    assert "401" in result["error"]


def test_list_incidents_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise(_self: Any, _path: str, **_kw: Any) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.pagerduty.client.httpx.Client.get", _raise)
    result = _client().list_incidents()
    assert result["success"] is False
    assert "connection refused" in result["error"]


# --- get_incident ---


def test_get_incident_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "incident": {
            "id": "P1ABC",
            "incident_number": 42,
            "title": "CPU spike",
            "description": "CPU > 90% for 5 minutes",
            "status": "triggered",
            "urgency": "high",
            "priority": {"id": "PR1", "name": "P1", "summary": "Critical"},
            "service": {"id": "SVC1", "summary": "Web", "type": "service_ref"},
            "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
            "teams": [{"id": "T1", "summary": "Backend", "type": "team_ref"}],
            "assignments": [{"assignee": {"id": "U1", "summary": "Alice", "type": "user_ref"}}],
            "acknowledgements": [
                {
                    "acknowledger": {"id": "U1", "summary": "Alice", "type": "user_ref"},
                    "at": "2024-01-01T00:02:00Z",
                }
            ],
            "created_at": "2024-01-01T00:00:00Z",
            "last_status_change_at": "2024-01-01T00:05:00Z",
            "resolved_at": None,
            "html_url": "https://app.pagerduty.com/incidents/P1ABC",
            "alert_counts": {"triggered": 1, "resolved": 0, "all": 1},
        }
    }
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_incident("P1ABC")
    assert result["success"] is True
    assert result["incident"]["description"] == "CPU > 90% for 5 minutes"
    assert result["incident"]["teams"][0]["summary"] == "Backend"
    assert result["incident"]["acknowledgements"][0]["at"] == "2024-01-01T00:02:00Z"


def test_get_incident_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "not found"}, 404),
    )
    result = _client().get_incident("bad-id")
    assert result["success"] is False
    assert "404" in result["error"]


# --- list_incident_log_entries ---


def test_list_incident_log_entries_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "log_entries": [
            {
                "id": "LE1",
                "type": "trigger_log_entry",
                "summary": "Triggered via monitoring",
                "created_at": "2024-01-01T00:00:00Z",
                "agent": {"id": "SVC1", "summary": "Datadog", "type": "service_ref"},
                "channel": {"type": "monitoring_tool"},
            },
            {
                "id": "LE2",
                "type": "acknowledge_log_entry",
                "summary": "Acknowledged by Alice",
                "created_at": "2024-01-01T00:02:00Z",
                "agent": {"id": "U1", "summary": "Alice", "type": "user_ref"},
                "channel": {"type": "web_app"},
            },
        ]
    }
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().list_incident_log_entries("P1ABC")
    assert result["success"] is True
    assert result["total"] == 2
    assert result["log_entries"][0]["type"] == "trigger_log_entry"
    assert result["log_entries"][1]["agent"]["summary"] == "Alice"


def test_list_incident_log_entries_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "forbidden"}, 403),
    )
    result = _client().list_incident_log_entries("P1ABC")
    assert result["success"] is False
    assert "403" in result["error"]


# --- get_oncalls ---


def test_get_oncalls_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "oncalls": [
            {
                "user": {"id": "U1", "summary": "Alice", "type": "user_reference"},
                "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
                "escalation_level": 1,
                "schedule": {"id": "S1", "summary": "Primary", "type": "schedule_ref"},
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-02T00:00:00Z",
            },
        ]
    }
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_oncalls()
    assert result["success"] is True
    assert result["total"] == 1
    assert result["oncalls"][0]["user"]["summary"] == "Alice"
    assert result["oncalls"][0]["escalation_level"] == 1


def test_get_oncalls_with_policy_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse({"oncalls": []})

    monkeypatch.setattr("app.services.pagerduty.client.httpx.Client.get", _fake_get)
    _client().get_oncalls(escalation_policy_ids=["EP1", "EP2"])
    assert captured["params"]["escalation_policy_ids[]"] == ["EP1", "EP2"]


def test_get_oncalls_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "unauthorized"}, 401),
    )
    result = _client().get_oncalls()
    assert result["success"] is False
    assert "401" in result["error"]


# --- list_services ---


def test_list_services_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "services": [
            {
                "id": "SVC1",
                "name": "Web App",
                "description": "Main web application",
                "status": "active",
                "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
                "teams": [{"id": "T1", "summary": "Backend", "type": "team_ref"}],
                "alert_creation": "create_alerts_and_incidents",
                "integrations": [
                    {
                        "id": "I1",
                        "name": "Datadog",
                        "type": "generic_events_api_inbound_integration",
                    }
                ],
                "html_url": "https://app.pagerduty.com/services/SVC1",
            },
        ]
    }
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().list_services()
    assert result["success"] is True
    assert result["total"] == 1
    assert result["services"][0]["name"] == "Web App"
    assert result["services"][0]["integrations"][0]["name"] == "Datadog"


def test_list_services_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "forbidden"}, 403),
    )
    result = _client().list_services()
    assert result["success"] is False
    assert "403" in result["error"]


# --- get_service ---


def test_get_service_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "service": {
            "id": "SVC1",
            "name": "Web App",
            "description": "Main web application",
            "status": "active",
            "escalation_policy": {"id": "EP1", "summary": "Prod", "type": "ep_ref"},
            "teams": [],
            "alert_creation": "create_alerts_and_incidents",
            "incident_urgency_rule": {"type": "constant", "urgency": "high"},
            "integrations": [
                {
                    "id": "I1",
                    "name": "Datadog",
                    "type": "generic_events_api_inbound_integration",
                    "vendor": {"id": "V1", "summary": "Datadog", "type": "vendor_ref"},
                }
            ],
            "html_url": "https://app.pagerduty.com/services/SVC1",
        }
    }
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )
    result = _client().get_service("SVC1")
    assert result["success"] is True
    assert result["service"]["name"] == "Web App"
    assert result["service"]["incident_urgency_rule"]["urgency"] == "high"
    assert result["service"]["integrations"][0]["vendor"]["summary"] == "Datadog"


def test_get_service_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.pagerduty.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "not found"}, 404),
    )
    result = _client().get_service("bad-id")
    assert result["success"] is False
    assert "404" in result["error"]


# --- close / context manager ---


def test_close_releases_http_client() -> None:
    c = _client()
    _ = c._get_client()
    assert c._client is not None
    c.close()
    assert c._client is None


def test_close_is_idempotent() -> None:
    c = _client()
    c.close()
    c.close()


def test_context_manager_closes_on_exit() -> None:
    with _client() as c:
        _ = c._get_client()
        assert c._client is not None
    assert c._client is None


def test_context_manager_closes_on_exception() -> None:
    c = _client()
    _ = c._get_client()
    with pytest.raises(ValueError), c:
        raise ValueError("test error")
    assert c._client is None


# --- make_pagerduty_client ---


def test_make_client_returns_client_with_valid_key() -> None:
    client = make_pagerduty_client("test-key")
    assert client is not None
    assert client.is_configured is True


def test_make_client_returns_none_for_empty_key() -> None:
    assert make_pagerduty_client("") is None
    assert make_pagerduty_client(None) is None


def test_make_client_returns_none_for_whitespace_key() -> None:
    assert make_pagerduty_client("   ") is None


def test_make_client_forwards_base_url() -> None:
    client = make_pagerduty_client("test-key", "https://custom.pd.com")
    assert client is not None
    assert client.config.base_url == "https://custom.pd.com"


def test_make_client_uses_default_base_url_when_none() -> None:
    client = make_pagerduty_client("test-key", None)
    assert client is not None
    assert client.config.base_url == "https://api.pagerduty.com"
