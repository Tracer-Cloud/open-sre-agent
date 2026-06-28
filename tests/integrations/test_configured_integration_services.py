"""Tests for the shared configured-integration-services helper.

This helper is the single source of truth shared by the welcome banner and the
REPL session, so it must return lowercase service keys, deduplicate, and never
raise (returning an empty list on failure).
"""

from __future__ import annotations

from typing import Any

from integrations import catalog


def test_returns_lowercase_service_keys_deduplicated(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        catalog,
        "load_env_integration_services",
        lambda: ["GitLab", "datadog", "gitlab", ""],
    )
    monkeypatch.setattr(catalog, "load_integrations", list)
    assert catalog.configured_integration_services() == ["gitlab", "datadog"]


def test_includes_active_store_integrations_and_dedupes_with_env(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        catalog,
        "load_env_integration_services",
        lambda: ["sentry", "gitlab"],
    )
    monkeypatch.setattr(
        catalog,
        "load_integrations",
        lambda: [
            {"service": "GitHub", "status": "active"},  # store-only (e.g. first-launch login)
            {"service": "gitlab", "status": "active"},  # duplicate of env entry
            {"service": "datadog", "status": "disabled"},  # inactive — ignored
            {"service": "", "status": "active"},  # ignored
        ],
    )
    assert catalog.configured_integration_services() == ["sentry", "gitlab", "github"]


def test_returns_empty_list_when_env_loader_raises(monkeypatch: Any) -> None:
    def _boom() -> list[str]:
        raise RuntimeError("env unreadable")

    monkeypatch.setattr(catalog, "load_env_integration_services", _boom)
    monkeypatch.setattr(catalog, "load_integrations", list)
    assert catalog.configured_integration_services() == []


def test_store_only_when_env_loader_raises(monkeypatch: Any) -> None:
    def _boom() -> list[str]:
        raise RuntimeError("env unreadable")

    monkeypatch.setattr(catalog, "load_env_integration_services", _boom)
    monkeypatch.setattr(
        catalog,
        "load_integrations",
        lambda: [{"service": "github", "status": "active"}],
    )
    assert catalog.configured_integration_services() == ["github"]


def test_empty_when_no_integrations(monkeypatch: Any) -> None:
    monkeypatch.setattr(catalog, "load_env_integration_services", list)
    monkeypatch.setattr(catalog, "load_integrations", list)
    assert catalog.configured_integration_services() == []


def test_configured_services_do_not_call_full_env_loader(monkeypatch: Any) -> None:
    def _full_loader_should_not_run() -> list[dict[str, Any]]:
        raise AssertionError("startup metadata path must not resolve env integrations")

    monkeypatch.setattr(catalog, "load_env_integrations", _full_loader_should_not_run)
    monkeypatch.setattr(catalog, "load_env_integration_services", lambda: ["gitlab"])
    monkeypatch.setattr(catalog, "load_integrations", list)

    assert catalog.configured_integration_services() == ["gitlab"]


def test_env_service_list_uses_plain_env_without_keyring(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "from-env")
    monkeypatch.delenv("POSTHOG_MCP_AUTH_TOKEN", raising=False)

    def _keyring_should_not_run(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("startup metadata path must not read keyring")

    monkeypatch.setattr("keyring.get_password", _keyring_should_not_run)

    assert "gitlab" in catalog.load_env_integration_services()


class TestConfiguredIntegrationHealth:
    """Offline health for the welcome banner: present vs. minimally usable.

    The banner must not imply a half-configured integration (e.g. a hosted MCP
    record saved without an API token) is connected, so this helper downgrades
    such records to ``"incomplete"`` without running any network verification.
    """

    def test_ok_when_classified_into_usable_config(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            catalog, "configured_integration_services", lambda: ["datadog", "gitlab"]
        )
        monkeypatch.setattr(catalog, "load_integrations", list)
        assert catalog.configured_integration_health() == [
            ("datadog", "ok"),
            ("gitlab", "ok"),
        ]

    def test_hosted_mcp_without_token_is_incomplete(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(catalog, "configured_integration_services", lambda: ["posthog_mcp"])
        monkeypatch.setattr(
            catalog,
            "load_integrations",
            lambda: [
                {
                    "service": "posthog_mcp",
                    "status": "active",
                    "instances": [
                        {
                            "name": "default",
                            "tags": {},
                            "credentials": {
                                "mode": "streamable-http",
                                "url": "https://mcp.posthog.com/mcp",
                                "auth_token": "",
                            },
                        }
                    ],
                }
            ],
        )
        assert catalog.configured_integration_health() == [("posthog_mcp", "incomplete")]

    def test_hosted_mcp_with_token_is_ok(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(catalog, "configured_integration_services", lambda: ["posthog_mcp"])
        monkeypatch.setattr(
            catalog,
            "load_integrations",
            lambda: [
                {
                    "service": "posthog_mcp",
                    "status": "active",
                    "instances": [
                        {
                            "name": "default",
                            "tags": {},
                            "credentials": {
                                "mode": "streamable-http",
                                "url": "https://mcp.posthog.com/mcp",
                                "auth_token": "phx_secret",
                            },
                        }
                    ],
                }
            ],
        )
        assert catalog.configured_integration_health() == [("posthog_mcp", "ok")]

    def test_stdio_mcp_without_token_is_ok(self, monkeypatch: Any) -> None:
        # stdio MCP authenticates via the local subprocess, so no token is needed.
        monkeypatch.setattr(catalog, "configured_integration_services", lambda: ["posthog_mcp"])
        monkeypatch.setattr(
            catalog,
            "load_integrations",
            lambda: [
                {
                    "service": "posthog_mcp",
                    "status": "active",
                    "instances": [
                        {
                            "name": "default",
                            "tags": {},
                            "credentials": {"mode": "stdio", "command": "npx", "auth_token": ""},
                        }
                    ],
                }
            ],
        )
        assert catalog.configured_integration_health() == [("posthog_mcp", "ok")]

    def test_non_mcp_empty_token_field_is_not_flagged(self, monkeypatch: Any) -> None:
        # Only the hosted-MCP token rule applies; an unrelated service that
        # classified successfully stays "ok" even if it lacks an auth_token key.
        monkeypatch.setattr(catalog, "configured_integration_services", lambda: ["github"])
        monkeypatch.setattr(catalog, "load_integrations", list)
        assert catalog.configured_integration_health() == [("github", "ok")]

    def test_empty_when_no_integrations(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(catalog, "configured_integration_services", list)
        assert catalog.configured_integration_health() == []

    def test_defaults_ok_when_store_load_raises(self, monkeypatch: Any) -> None:
        def _boom() -> list[dict[str, Any]]:
            raise RuntimeError("store unreadable")

        monkeypatch.setattr(catalog, "configured_integration_services", lambda: ["datadog"])
        monkeypatch.setattr(catalog, "load_integrations", _boom)
        # Resolution failure must not crash the banner or alarm the user: when
        # health can't be determined offline, every service falls back to "ok".
        assert catalog.configured_integration_health() == [("datadog", "ok")]

    def test_health_does_not_call_effective_resolution(self, monkeypatch: Any) -> None:
        def _resolve_should_not_run() -> dict[str, Any]:
            raise AssertionError("startup health must not resolve secrets")

        monkeypatch.setattr(catalog, "configured_integration_services", lambda: ["datadog"])
        monkeypatch.setattr(catalog, "load_integrations", list)
        monkeypatch.setattr(catalog, "resolve_effective_integrations", _resolve_should_not_run)

        assert catalog.configured_integration_health() == [("datadog", "ok")]
