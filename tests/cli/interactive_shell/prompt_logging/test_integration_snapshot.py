"""Tests for per-turn integration snapshots on analytics capture."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.cli.interactive_shell.prompt_logging.integration_snapshot import (
    build_turn_integration_snapshot,
)
from app.cli.interactive_shell.runtime.session import ReplSession


def test_build_turn_integration_snapshot_empty_when_unconfigured() -> None:
    session = ReplSession()
    session.configured_integrations_known = True
    session.configured_integrations = ()

    snapshot = build_turn_integration_snapshot(session)

    assert snapshot == {
        "connected_integrations": [],
        "connected_integrations_count": 0,
        "configured_integrations": [],
        "integration_snapshot_source": "runtime_config",
    }


def test_build_turn_integration_snapshot_uses_session_configured_slugs(
    monkeypatch: Any,
) -> None:
    session = ReplSession()
    session.configured_integrations_known = True
    session.configured_integrations = ("datadog", "github")
    session.resolved_integrations_cache = {
        "datadog": {"api_key": "x", "app_key": "y", "connection_verified": True},
        "github": {"access_token": "token", "connection_verified": True},
    }

    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.integration_snapshot.get_available_tools",
        lambda _resolved: [
            MagicMock(source="datadog"),
            MagicMock(source="github"),
        ],
    )

    snapshot = build_turn_integration_snapshot(session)

    assert snapshot["configured_integrations"] == ["datadog", "github"]
    assert snapshot["connected_integrations"] == ["datadog", "github"]
    assert snapshot["connected_integrations_count"] == 2


def test_build_turn_integration_snapshot_excludes_unavailable_tools(
    monkeypatch: Any,
) -> None:
    session = ReplSession()
    session.configured_integrations_known = True
    session.configured_integrations = ("datadog", "grafana")
    session.resolved_integrations_cache = {
        "datadog": {"api_key": "x", "app_key": "y", "connection_verified": True},
        "grafana": {"endpoint": "https://grafana.example.com", "api_key": "glsa"},
    }

    monkeypatch.setattr(
        "app.cli.interactive_shell.prompt_logging.integration_snapshot.get_available_tools",
        lambda _resolved: [MagicMock(source="datadog")],
    )

    snapshot = build_turn_integration_snapshot(session)

    assert snapshot["configured_integrations"] == ["datadog", "grafana"]
    assert snapshot["connected_integrations"] == ["datadog"]
    assert snapshot["connected_integrations_count"] == 1
