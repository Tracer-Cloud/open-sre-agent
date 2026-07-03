"""Tests for Agent.resolve_integrations."""

from __future__ import annotations

from typing import Any

import pytest

from core.agent import Agent
from core.agent_harness.session import Session


def test_resolve_integrations_returns_cached_configs_without_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.resolved_integrations_cache = {"slack": {"webhook_url": "https://example/hook"}}

    def _unexpected(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("resolve_integrations() must not run on a cache hit")

    monkeypatch.setattr(
        "core.agent_harness.integrations.resolution.resolve_integrations",
        _unexpected,
    )

    assert Agent.resolve_integrations(session) == {
        "slack": {"webhook_url": "https://example/hook"},
    }


def test_resolve_integrations_resolves_on_cache_miss_and_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    monkeypatch.setattr(
        "core.agent_harness.integrations.resolution.resolve_integrations",
        lambda *_args, **_kwargs: {"datadog": {"api_key": "dd-key"}},
    )

    resolved = Agent.resolve_integrations(session)

    assert resolved == {"datadog": {"api_key": "dd-key"}}
    assert session.resolved_integrations_cache == {"datadog": {"api_key": "dd-key"}}


def test_resolve_and_cache_integrations_delegates_to_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_harness.integrations.resolution import resolve_and_cache_integrations

    session = Session()
    calls: list[Session] = []

    def _fake(session_arg: Session) -> dict[str, Any]:
        calls.append(session_arg)
        return {"github": {"token": "ghp_test"}}

    monkeypatch.setattr(Agent, "resolve_integrations", staticmethod(_fake))

    assert resolve_and_cache_integrations(session) == {"github": {"token": "ghp_test"}}
    assert calls == [session]


def test_resolve_integrations_does_not_cache_empty_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty resolve must not be cached, so a later turn can retry.
    session = Session()
    monkeypatch.setattr(
        "core.agent_harness.integrations.resolution.resolve_integrations",
        lambda *_args, **_kwargs: {},
    )

    assert Agent.resolve_integrations(session) == {}
    assert session.resolved_integrations_cache is None


def test_resolve_integrations_reresolves_metadata_only_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cache holding only runtime metadata (keys starting with "_") is not a hit.
    session = Session()
    session.resolved_integrations_cache = {"_auth_token": "tok"}
    monkeypatch.setattr(
        "core.agent_harness.integrations.resolution.resolve_integrations",
        lambda *_args, **_kwargs: {"datadog": {"api_key": "dd-key"}},
    )

    resolved = Agent.resolve_integrations(session)

    assert resolved["datadog"] == {"api_key": "dd-key"}
    assert session.resolved_integrations_cache["datadog"] == {"api_key": "dd-key"}
