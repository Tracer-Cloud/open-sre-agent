"""Tests for the investigation resolve-integrations stage wrapper."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.integration_resolution import IntegrationResolutionResult
from tools.investigation.stages.resolve_integrations import node


class _Tracker:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.completed: list[tuple[str, dict[str, Any]]] = []

    def start(self, node_name: str, message: str) -> None:
        self.started.append((node_name, message))

    def complete(self, node_name: str, **kwargs: Any) -> None:
        self.completed.append((node_name, kwargs))


def test_resolve_integrations_wraps_platform_result_with_progress(monkeypatch: Any) -> None:
    tracker = _Tracker()
    monkeypatch.setattr(node, "get_tracker", lambda: tracker)
    monkeypatch.setattr(
        node,
        "resolve_integrations_with_metadata",
        lambda _state: IntegrationResolutionResult(
            resolved_integrations={"datadog": {"site": "datadoghq.com"}},
            progress_message="Resolved local integrations from store: ['datadog']",
        ),
    )

    updates = node.resolve_integrations({"org_id": "org-1"})  # type: ignore[arg-type]

    assert updates == {"resolved_integrations": {"datadog": {"site": "datadoghq.com"}}}
    assert tracker.started == [("resolve_integrations", "Fetching org integrations")]
    assert tracker.completed == [
        (
            "resolve_integrations",
            {
                "fields_updated": ["resolved_integrations"],
                "message": "Resolved local integrations from store: ['datadog']",
            },
        )
    ]


def test_resolve_integrations_quiet_skips_progress(monkeypatch: Any) -> None:
    tracker = _Tracker()
    monkeypatch.setattr(node, "get_tracker", lambda: tracker)
    monkeypatch.setattr(
        node,
        "resolve_integrations_with_metadata",
        lambda _state: IntegrationResolutionResult(
            resolved_integrations={"sentry": {}},
            progress_message="Resolved integrations",
        ),
    )

    resolved = node.resolve_integrations_quiet({})  # type: ignore[arg-type]

    assert resolved == {"sentry": {}}
    assert tracker.started == []
    assert tracker.completed == []


def test_resolve_integrations_keeps_idempotency_guard_before_progress(monkeypatch: Any) -> None:
    tracker = _Tracker()
    monkeypatch.setattr(node, "get_tracker", lambda: tracker)

    def _unexpected_resolve(_state: dict[str, Any]) -> IntegrationResolutionResult:
        raise AssertionError("platform resolver should not be called")

    monkeypatch.setattr(node, "resolve_integrations_with_metadata", _unexpected_resolve)

    updates = node.resolve_integrations(  # type: ignore[arg-type]
        {"resolved_integrations": {"github": {}}}
    )

    assert updates == {"resolved_integrations": {"github": {}}}
    assert tracker.started == []
    assert tracker.completed == []
