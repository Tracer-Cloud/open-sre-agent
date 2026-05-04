"""Tests for AlertmanagerClient."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.integrations.config_models import AlertmanagerIntegrationConfig
from app.services.alertmanager.client import (
    AlertmanagerClient,
    make_alertmanager_client,
)

AlertmanagerConfig = AlertmanagerIntegrationConfig


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


def _client(**kwargs: Any) -> AlertmanagerClient:
    default_config = {
        "base_url": "https://alertmanager.example.com",
        "bearer_token": "test-token",
        "username": "",
        "password": "",
    }
    default_config.update(kwargs)
    return AlertmanagerClient(AlertmanagerConfig(**default_config))


# --- Config ---


def test_bearer_token_auth_includes_header() -> None:
    c = _client(bearer_token="my-secret-token")
    assert c.config.headers["Authorization"] == "Bearer my-secret-token"


def test_basic_auth_config_set() -> None:
    c = _client(bearer_token="", username="admin", password="secret123")
    assert c.config.username == "admin"
    assert c.config.password == "secret123"
    assert c.config.bearer_token == ""
    assert c.config.basic_auth == ("admin", "secret123")


def test_basic_auth_headers_do_not_include_bearer() -> None:
    c = _client(bearer_token="", username="admin", password="secret123")
    assert "Authorization" not in c.config.headers


def test_is_configured_with_bearer_token() -> None:
    c = _client(bearer_token="test-token")
    assert c.is_configured is True


def test_is_configured_with_basic_auth() -> None:
    c = _client(bearer_token="", username="user", password="pass")
    assert c.is_configured is True


def test_is_configured_without_credentials() -> None:
    c = _client(bearer_token="", username="", password="")
    # Has base_url so is configured
    assert c.is_configured is True


def test_is_configured_false_without_base_url() -> None:
    client = AlertmanagerClient(AlertmanagerConfig(base_url="", bearer_token="token"))
    assert client.is_configured is False


def test_dual_auth_rejected() -> None:
    with pytest.raises(ValueError, match="both bearer_token and username"):
        AlertmanagerConfig(
            base_url="https://example.com",
            bearer_token="token",
            username="user",
            password="pass",
        )


def test_get_client_forwards_auth_config_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            return None

    monkeypatch.setattr("app.services.alertmanager.client.httpx.Client", _FakeClient)

    c = _client(bearer_token="", username="admin", password="secret123")
    _ = c._get_client()

    assert captured["base_url"] == "https://alertmanager.example.com"
    assert captured["headers"] == {"Content-Type": "application/json"}
    assert captured["auth"] == ("admin", "secret123")
    assert captured["timeout"] == 30


# --- get_status ---


def test_get_status_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "cluster": {
            "status": "ok",
            "peers": ["peer1", "peer2"],
        }
    }

    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )

    result = _client().get_status()
    assert result["success"] is True
    assert result["status"]["cluster"]["status"] == "ok"


def test_get_status_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"message": "forbidden"}, 403),
    )

    result = _client().get_status()
    assert result["success"] is False
    assert "403" in result["error"]


def test_get_status_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise(_self: Any, _path: str, **_kw: Any) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.alertmanager.client.httpx.Client.get", _raise)

    result = _client().get_status()
    assert result["success"] is False
    assert "connection refused" in result["error"]


def test_get_status_calls_correct_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, path: str, **_kwargs: Any) -> _FakeResponse:
        captured["path"] = path
        return _FakeResponse({"cluster": {"status": "ok"}})

    monkeypatch.setattr("app.services.alertmanager.client.httpx.Client.get", _fake_get)

    _client().get_status()
    assert captured["path"] == "/api/v2/status"


# --- list_alerts ---


def test_list_alerts_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "fingerprint": "abc123",
            "status": {"state": "firing", "inhibitedBy": [], "silencedBy": []},
            "labels": {"alertname": "HighErrorRate", "severity": "critical"},
            "annotations": {"description": "Error rate > 50%"},
            "startsAt": "2026-05-02T10:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus.example.com",
        }
    ]

    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )

    result = _client().list_alerts()
    assert result["success"] is True
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["fingerprint"] == "abc123"
    assert result["alerts"][0]["status"] == "firing"


def test_list_alerts_with_filter_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(_self: Any, _path: str, **kwargs: Any) -> _FakeResponse:
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse([])

    monkeypatch.setattr("app.services.alertmanager.client.httpx.Client.get", _fake_get)

    _client().list_alerts(active=True, silenced=False, filter_labels=['alertname="Test"'])

    assert captured["params"]["active"] == "true"
    assert captured["params"]["silenced"] == "false"
    assert captured["params"]["filter"] == ['alertname="Test"']


def test_list_alerts_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "unauthorized"}, 401),
    )

    result = _client().list_alerts()
    assert result["success"] is False
    assert "401" in result["error"]


def test_list_alerts_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse([]),
    )

    result = _client().list_alerts()
    assert result["success"] is True
    assert result["alerts"] == []
    assert result["total"] == 0


def test_list_alerts_unexpected_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"not": "a list"}),
    )

    result = _client().list_alerts()
    assert result["success"] is False
    assert "Unexpected response format" in result["error"]


def test_list_alerts_limit_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "fingerprint": f"fp{i}",
            "status": {"state": "firing", "inhibitedBy": [], "silencedBy": []},
        }
        for i in range(10)
    ]

    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )

    result = _client().list_alerts(limit=5)
    assert result["success"] is True
    assert result["total"] == 5


# --- list_silences ---


def test_list_silences_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "id": "silence-123",
            "status": {"state": "active"},
            "matchers": [{"name": "service", "value": "api", "isRegex": False}],
            "comment": "Planned maintenance",
            "createdBy": "admin@example.com",
            "startsAt": "2026-05-02T10:00:00Z",
            "endsAt": "2026-05-02T12:00:00Z",
        },
        {
            "id": "silence-456",
            "status": {"state": "expired"},
            "matchers": [],
            "comment": "Old maintenance",
            "createdBy": "admin@example.com",
            "startsAt": "2026-05-01T10:00:00Z",
            "endsAt": "2026-05-01T12:00:00Z",
        },
    ]

    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )

    result = _client().list_silences()

    assert result["success"] is True
    assert result["total"] == 2
    assert result["silences"][0]["id"] == "silence-123"
    assert result["silences"][0]["comment"] == "Planned maintenance"
    assert result["active_silences"] == [result["silences"][0]]


def test_list_silences_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "forbidden"}, 403),
    )

    result = _client().list_silences()

    assert result["success"] is False
    assert "HTTP 403" in result["error"]


def test_list_silences_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse([]),
    )

    result = _client().list_silences()

    assert result["success"] is True
    assert result["silences"] == []
    assert result["active_silences"] == []
    assert result["total"] == 0


def test_list_silences_unexpected_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"silences": []}),
    )

    result = _client().list_silences()

    assert result["success"] is False
    assert "Unexpected response format" in result["error"]


def test_list_silences_limit_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "id": f"silence-{i}",
            "status": {"state": "active"},
        }
        for i in range(10)
    ]

    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse(payload),
    )

    result = _client().list_silences(limit=5)

    assert result["success"] is True
    assert result["total"] == 5
    assert len(result["active_silences"]) == 5


# --- Context Manager ---


def test_close_releases_http_client() -> None:
    c = _client()
    mock_client = MagicMock()
    c._client = mock_client

    assert c._client is not None
    c.close()
    assert c._client is None
    mock_client.close.assert_called_once()


def test_close_is_idempotent() -> None:
    c = _client()
    mock_client = MagicMock()
    c._client = mock_client

    c.close()
    c.close()  # Should not raise
    mock_client.close.assert_called_once()


def test_context_manager_closes_on_exit() -> None:
    c = _client()
    mock_client = MagicMock()
    c._client = mock_client

    with c:
        assert c._client is not None

    assert c._client is None
    mock_client.close.assert_called_once()


def _raise_value_error() -> None:
    raise ValueError("test error")


def test_context_manager_closes_on_exception() -> None:
    c = _client()
    mock_client = MagicMock()
    c._client = mock_client

    with pytest.raises(ValueError), c:
        _raise_value_error()

    assert c._client is None
    mock_client.close.assert_called_once()


def test_context_manager_returns_self() -> None:
    c = _client()
    with c as entered:
        assert entered is c


# --- Factory Function ---


def test_make_client_with_bearer_token() -> None:
    client = make_alertmanager_client(
        base_url="https://alertmanager.example.com",
        bearer_token="test-token",
    )
    assert client is not None
    assert client.is_configured is True
    assert client.config.bearer_token == "test-token"


def test_make_client_with_basic_auth() -> None:
    client = make_alertmanager_client(
        base_url="https://alertmanager.example.com",
        username="admin",
        password="secret",
    )
    assert client is not None
    assert client.config.username == "admin"
    assert client.config.password == "secret"


def test_make_client_returns_none_when_no_url() -> None:
    assert make_alertmanager_client("") is None
    assert make_alertmanager_client(None) is None
    assert make_alertmanager_client("   ") is None


def test_make_client_normalizes_url() -> None:
    client = make_alertmanager_client(
        base_url="https://alertmanager.example.com/",
        bearer_token="token",
    )
    assert client is not None
    assert client.config.base_url == "https://alertmanager.example.com"


def test_make_client_strips_whitespace() -> None:
    client = make_alertmanager_client(
        base_url="  https://alertmanager.example.com  ",
        bearer_token="token",
    )
    assert client is not None
    assert client.config.base_url == "https://alertmanager.example.com"


# --- Probe Access ---


def test_probe_access_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"cluster": {"status": "ok"}}),
    )

    result = _client().probe_access()
    assert result.status == "passed"
    assert "Connected to Alertmanager" in result.detail


def test_probe_access_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.alertmanager.client.httpx.Client.get",
        lambda _self, _path, **_kw: _FakeResponse({"error": "unauthorized"}, 401),
    )

    result = _client().probe_access()
    assert result.status == "failed"
    assert "Status check failed" in result.detail


def test_probe_access_not_configured() -> None:
    client = AlertmanagerClient(AlertmanagerConfig(base_url="", bearer_token="token"))
    result = client.probe_access()
    assert result.status == "missing"
    assert "Missing base_url" in result.detail
