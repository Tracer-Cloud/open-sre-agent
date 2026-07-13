"""Tests for :class:`core.agent_harness.harness.AgentHarness`.

Covers the startup responsibilities the harness consolidates: env
resolution, session bootstrap/resume (delegated to
:class:`~core.agent_harness.session.lifecycle.SessionManager`), on-demand
integration resolution, and context loading — plus the ordering
``startup()`` runs them in.
"""

from __future__ import annotations

from typing import Any

import core.agent_harness.harness as harness_module
from surfaces.interactive_shell.session import Session


class _FakeSessionManager:
    """Records create()/resolve() calls instead of touching real storage."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.create_calls: list[dict[str, Any]] = []
        self.resolve_calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Session:
        self.create_calls.append(kwargs)
        return self.session

    def resolve(self, session_id: str, **kwargs: Any) -> Session:
        self.resolve_calls.append({"session_id": session_id, **kwargs})
        return self.session


def test_resolve_env_variables_calls_load_dotenv(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(harness_module, "load_dotenv", lambda **kwargs: calls.append(kwargs))
    harness = harness_module.AgentHarness()

    harness.resolve_env_variables()

    assert calls == [{"override": False}]


def test_resolve_env_variables_skipped_when_load_env_false(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(harness_module, "load_dotenv", lambda **kwargs: calls.append(kwargs))
    harness = harness_module.AgentHarness(harness_module.HarnessConfig(load_env=False))

    harness.resolve_env_variables()

    assert calls == []


def test_load_or_create_session_creates_when_no_session_id() -> None:
    manager = _FakeSessionManager(Session())
    harness = harness_module.AgentHarness(harness_module.HarnessConfig(session_manager=manager))  # type: ignore[arg-type]

    session = harness.load_or_create_session()

    assert session is manager.session
    assert manager.create_calls == [
        {
            "hydrate_integrations": True,
            "warm_integrations": False,
            "persistent_tasks": True,
            "open_storage": True,
        }
    ]
    assert manager.resolve_calls == []


def test_load_or_create_session_resolves_when_session_id_given() -> None:
    manager = _FakeSessionManager(Session())
    harness = harness_module.AgentHarness(
        harness_module.HarnessConfig(session_id="abc123", session_manager=manager)  # type: ignore[arg-type]
    )

    session = harness.load_or_create_session()

    assert session is manager.session
    assert manager.resolve_calls == [
        {
            "session_id": "abc123",
            "hydrate_integrations": True,
            "warm_integrations": True,
            "persistent_tasks": True,
        }
    ]
    assert manager.create_calls == []


def test_load_or_create_session_forwards_explicit_warm_integrations() -> None:
    manager = _FakeSessionManager(Session())
    harness = harness_module.AgentHarness(
        harness_module.HarnessConfig(warm_integrations=True, session_manager=manager)  # type: ignore[arg-type]
    )

    harness.load_or_create_session()

    assert manager.create_calls == [
        {
            "hydrate_integrations": True,
            "persistent_tasks": True,
            "open_storage": True,
            "warm_integrations": True,
        }
    ]


def test_resolve_integrations_delegates_to_session() -> None:
    session = Session()
    session.resolved_integrations_cache = {"datadog": {"api_key": "x"}}
    harness = harness_module.AgentHarness()

    resolved = harness.resolve_integrations(session)

    assert resolved == {"datadog": {"api_key": "x"}}


def test_load_context_returns_configured_prompts() -> None:
    sentinel = object()
    harness = harness_module.AgentHarness(harness_module.HarnessConfig(prompts=sentinel))  # type: ignore[arg-type]

    assert harness.load_context() is sentinel


def test_load_context_returns_none_by_default() -> None:
    harness = harness_module.AgentHarness()

    assert harness.load_context() is None


def test_startup_runs_env_then_session_then_context(monkeypatch: Any) -> None:
    session = Session()
    manager = _FakeSessionManager(session)
    sentinel = object()
    order: list[str] = []
    monkeypatch.setattr(
        harness_module.AgentHarness,
        "resolve_env_variables",
        lambda _self: order.append("env"),
    )
    original_load_session = harness_module.AgentHarness.load_or_create_session

    def _tracked_load_session(self: harness_module.AgentHarness) -> Session:
        order.append("session")
        return original_load_session(self)

    monkeypatch.setattr(
        harness_module.AgentHarness, "load_or_create_session", _tracked_load_session
    )
    original_context = harness_module.AgentHarness.load_context

    def _tracked_context(self: harness_module.AgentHarness) -> Any:
        order.append("context")
        return original_context(self)

    monkeypatch.setattr(harness_module.AgentHarness, "load_context", _tracked_context)

    harness = harness_module.AgentHarness(
        harness_module.HarnessConfig(prompts=sentinel, session_manager=manager)  # type: ignore[arg-type]
    )
    result = harness.startup()

    assert order == ["env", "session", "context"]
    assert result.session is session
    assert result.prompts is sentinel
