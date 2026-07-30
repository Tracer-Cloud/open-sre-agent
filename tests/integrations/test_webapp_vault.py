"""Tests for silo → opensre-webapp integrations vault client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import integrations.webapp_vault as vault
from config.constants.billing import (
    MACHINE_SECRET_ENV,
    ORGANIZATION_ID_ENV,
    USAGE_SECRET_ENV,
    WEBAPP_URL_ENV,
)


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
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    monkeypatch.delenv(USAGE_SECRET_ENV, raising=False)
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    assert vault.fetch_webapp_org_integrations() is None
    assert vault.webapp_vault_configured() is False


def test_shared_secret_alone_is_never_sent_to_the_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This route accepts only a machine token, so a silo holding just the
    shared secret must not call it — a 401 there is indistinguishable from
    "this org has no integrations" and silently hides vault-backed ones."""
    # Arrange: fully configured except for the machine secret.
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setenv(USAGE_SECRET_ENV, "SHARED-SECRET-LEAK-MARKER")
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(vault.httpx, "get", lambda url, **kw: sent.append({"url": url, **kw}))

    # Act
    records = vault.fetch_webapp_org_integrations()

    # Assert: no request at all, so the marker cannot have been transmitted.
    assert records is None
    assert sent == []
    assert "SHARED-SECRET-LEAK-MARKER" not in repr(sent)
    assert vault.webapp_vault_configured() is False


def test_fetches_and_normalizes_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_test")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setattr(vault, "webapp_machine_token", lambda: "mt_vault")

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
    assert calls[0]["headers"]["Authorization"] == "Bearer mt_vault"


def test_configured_requires_the_machine_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange / Act / Assert: url + org + machine secret is the real contract.
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    assert vault.webapp_vault_configured() is False

    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_test")
    assert vault.webapp_vault_configured() is True


def test_http_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example.com")
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_test")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    monkeypatch.setattr(vault, "webapp_machine_token", lambda: "mt_vault")
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
