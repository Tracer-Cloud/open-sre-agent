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
    assert catalog.configured_integration_services() == ["gitlab", "datadog"]


def test_returns_empty_list_when_loader_raises(monkeypatch: Any) -> None:
    def _boom() -> list[dict[str, Any]]:
        raise RuntimeError("env unreadable")

    monkeypatch.setattr(catalog, "load_env_integrations", _boom)
    assert catalog.configured_integration_services() == []


def test_empty_when_no_integrations(monkeypatch: Any) -> None:
    monkeypatch.setattr(catalog, "load_env_integrations", list)
    assert catalog.configured_integration_services() == []
