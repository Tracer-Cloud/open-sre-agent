"""Tests for the shared configured-integration-services helper.

This helper is the single source of truth shared by the welcome banner and the
REPL session, so it must return lowercase service keys, deduplicate, and never
raise (returning an empty list on failure).
"""

from __future__ import annotations

from typing import Any

from app.integrations import catalog


def test_returns_lowercase_service_keys_deduplicated(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        catalog,
        "load_env_integrations",
        lambda: [
            {"service": "GitLab"},
            {"service": "datadog"},
            {"service": "gitlab"},  # duplicate (case-insensitive)
            {"service": ""},  # ignored
        ],
    )
    monkeypatch.setattr(catalog, "load_integrations", list)
    assert catalog.configured_integration_services() == ["gitlab", "datadog"]


def test_includes_active_store_integrations_and_dedupes_with_env(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        catalog,
        "load_env_integrations",
        lambda: [{"service": "sentry"}, {"service": "gitlab"}],
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
    def _boom() -> list[dict[str, Any]]:
        raise RuntimeError("env unreadable")

    monkeypatch.setattr(catalog, "load_env_integrations", _boom)
    monkeypatch.setattr(catalog, "load_integrations", list)
    assert catalog.configured_integration_services() == []


def test_store_only_when_env_loader_raises(monkeypatch: Any) -> None:
    def _boom() -> list[dict[str, Any]]:
        raise RuntimeError("env unreadable")

    monkeypatch.setattr(catalog, "load_env_integrations", _boom)
    monkeypatch.setattr(
        catalog,
        "load_integrations",
        lambda: [{"service": "github", "status": "active"}],
    )
    assert catalog.configured_integration_services() == ["github"]


def test_empty_when_no_integrations(monkeypatch: Any) -> None:
    monkeypatch.setattr(catalog, "load_env_integrations", list)
    monkeypatch.setattr(catalog, "load_integrations", list)
    assert catalog.configured_integration_services() == []
