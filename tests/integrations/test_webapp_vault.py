"""Tests for silo → opensre-webapp integrations vault client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import integrations.webapp_vault as vault
from config.constants.billing import ORGANIZATION_ID_ENV, USAGE_SECRET_ENV, WEBAPP_URL_ENV


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_unconfigured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WEBAPP_URL_ENV, raising=False)
    monkeypatch.delenv(USAGE_SECRET_ENV, raising=False)
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    assert vault.fetch_webapp_org_integrations() is None
    assert vault.webapp_vault_configured() is False


def test_fetches_and_normalizes_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(USAGE_SECRET_ENV, "sekrit")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")

    calls: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append({"url": url, **kwargs})
        return _FakeResponse(
            200,
            {
                "success": True,
                "data": [
                    {
                        "id": "int_gh",
                        "service": "github",
                        "status": "active",
                        "name": "default",
                        "credentials": {
                            "auth_token": "ghp_x",
                            "url": "https://api.githubcopilot.com/mcp/",
                            "mode": "streamable-http",
                        },
                    },
                    {"service": "broken", "credentials": "not-a-dict"},
                ],
            },
        )

    monkeypatch.setattr(vault.httpx, "get", fake_get)

    records = vault.fetch_webapp_org_integrations()

    assert records is not None
    assert len(records) == 1
    assert records[0]["service"] == "github"
    assert records[0]["credentials"]["auth_token"] == "ghp_x"
    assert calls[0]["url"] == "https://app.example.com/api/agent/integrations"
    assert calls[0]["params"]["organizationId"] == "org_1"
    assert calls[0]["headers"]["Authorization"] == "Bearer sekrit"


def test_http_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(USAGE_SECRET_ENV, "sekrit")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setattr(
        vault.httpx,
        "get",
        lambda *_a, **_k: (_ for _ in ()).throw(httpx.ConnectError("down")),
    )
    assert vault.fetch_webapp_org_integrations() is None


def test_resolve_integrations_merges_webapp_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway warm path: vault github appears in resolved integrations."""
    import platform.harness_ports as ports
    from integrations.harness_adapters import register_harness_adapters

    register_harness_adapters()
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(USAGE_SECRET_ENV, "sekrit")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setattr(
        "integrations.webapp_vault.fetch_webapp_org_integrations",
        lambda: [
            {
                "id": "int_gh",
                "service": "github",
                "status": "active",
                "name": "default",
                "credentials": {
                    "auth_token": "ghp_from_vault",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "mode": "streamable-http",
                },
            }
        ],
    )
    monkeypatch.setattr(ports, "_load_integrations", lambda: [])
    monkeypatch.setattr(ports, "_load_env_integrations", lambda: [])

    result = ports.resolve_integrations_with_metadata({})
    assert "github" in result.resolved_integrations
    gh = result.resolved_integrations["github"]
    assert getattr(gh, "auth_token", None) == "ghp_from_vault" or (
        isinstance(gh, dict) and gh.get("auth_token") == "ghp_from_vault"
    )
