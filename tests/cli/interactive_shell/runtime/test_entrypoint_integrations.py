"""Tests for hydrating configured integrations onto the REPL session at boot.

Without this the agent cannot answer "is X installed?" and the integration
guards stay dead because ``configured_integrations_known`` never flips to True.
"""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from app.cli.interactive_shell.runtime import entrypoint
from app.cli.interactive_shell.runtime.session import ReplSession


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def test_hydrate_populates_session_from_effective_resolution(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "app.integrations.verify.resolve_effective_integrations",
        lambda: {"gitlab": {}, "datadog": {}},
    )
    session = ReplSession()
    entrypoint._hydrate_configured_integrations(session)
    assert session.configured_integrations_known is True
    # Resolution covers env + local store and is returned in sorted order.
    assert session.configured_integrations == ("datadog", "gitlab")


def test_hydrate_marks_known_even_when_none_configured(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "app.integrations.verify.resolve_effective_integrations",
        dict,
    )
    session = ReplSession()
    entrypoint._hydrate_configured_integrations(session)
    assert session.configured_integrations_known is True
    assert session.configured_integrations == ()


def test_warm_resolved_integrations_populates_cache(monkeypatch: Any) -> None:
    resolved = {"datadog": {"site": "datadoghq.com"}, "grafana": {"url": "http://localhost"}}
    monkeypatch.setattr(
        "app.agent.stages.resolve_integrations.resolve_integrations",
        lambda _state: {"resolved_integrations": resolved},
    )
    session = ReplSession()
    session.warm_resolved_integrations()
    assert session.resolved_integrations_cache == resolved


def test_warm_resolved_integrations_is_idempotent(monkeypatch: Any) -> None:
    calls: list[str] = []

    def _resolve(_state: dict[str, Any]) -> dict[str, Any]:
        calls.append("resolve")
        return {"resolved_integrations": {"github": {}}}

    monkeypatch.setattr(
        "app.agent.stages.resolve_integrations.resolve_integrations",
        _resolve,
    )
    session = ReplSession()
    session.warm_resolved_integrations()
    session.warm_resolved_integrations()
    assert calls == ["resolve"]


def test_warm_resolved_integrations_skips_empty_cache(monkeypatch: Any) -> None:
    calls: list[str] = []

    def _resolve(_state: dict[str, Any]) -> dict[str, Any]:
        calls.append("resolve")
        return {"resolved_integrations": {}}

    monkeypatch.setattr(
        "app.agent.stages.resolve_integrations.resolve_integrations",
        _resolve,
    )
    session = ReplSession()
    session.warm_resolved_integrations()
    assert session.resolved_integrations_cache is None
    session.warm_resolved_integrations()
    assert calls == ["resolve", "resolve"]


def test_warm_resolved_integrations_resets_tracker_on_resolve_failure(
    monkeypatch: Any,
) -> None:
    resets: list[str] = []

    def _resolve(_state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("resolve failed")

    monkeypatch.setattr(
        "app.agent.stages.resolve_integrations.resolve_integrations",
        _resolve,
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.output.set_silent_tracker",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.output.reset_tracker",
        lambda: resets.append("reset"),
    )

    session = ReplSession()
    session.warm_resolved_integrations()

    assert session.resolved_integrations_cache is None
    assert resets == ["reset"]


def test_hydrate_entrypoint_does_not_warm_before_prompt(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "app.integrations.verify.resolve_effective_integrations",
        lambda: {"datadog": {}},
    )
    resolve_calls: list[str] = []

    def _resolve(_state: dict[str, Any]) -> dict[str, Any]:
        resolve_calls.append("resolve")
        return {"resolved_integrations": {"datadog": {"site": "datadoghq.com"}}}

    monkeypatch.setattr(
        "app.agent.stages.resolve_integrations.resolve_integrations",
        _resolve,
    )
    session = ReplSession()
    entrypoint._hydrate_configured_integrations(session)
    assert session.configured_integrations_known is True
    assert session.resolved_integrations_cache is None
    assert resolve_calls == []


def test_schedule_warm_resolved_integrations_runs_in_background(
    monkeypatch: Any,
) -> None:
    import asyncio

    warmed = asyncio.Event()

    def _warm(self: ReplSession) -> None:
        warmed.set()

    monkeypatch.setattr(ReplSession, "warm_resolved_integrations", _warm)

    async def _run() -> None:
        session = ReplSession()
        session.schedule_warm_resolved_integrations()
        await asyncio.wait_for(warmed.wait(), timeout=1.0)

    asyncio.run(_run())


def test_hydrate_leaves_unknown_on_failure(monkeypatch: Any) -> None:
    def _boom() -> dict[str, Any]:
        raise RuntimeError("catalog blew up")

    monkeypatch.setattr(
        "app.integrations.verify.resolve_effective_integrations",
        _boom,
    )
    session = ReplSession()
    entrypoint._hydrate_configured_integrations(session)
    assert session.configured_integrations_known is False
    assert session.configured_integrations == ()


def test_gate_error_blocks_startup_without_bypass(monkeypatch: Any) -> None:
    """On an unexpected gate error we must NOT fail open into the REPL unless an
    explicit bypass applies."""
    monkeypatch.setattr(
        "app.cli.first_launch_github.should_require_github_login",
        lambda: (_ for _ in ()).throw(RuntimeError("gate broke")),
    )
    monkeypatch.setattr(entrypoint, "_github_login_explicitly_bypassed", lambda: False)

    assert entrypoint._maybe_require_github_login(_console()) is False


def test_gate_error_allows_startup_with_bypass(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "app.cli.first_launch_github.should_require_github_login",
        lambda: (_ for _ in ()).throw(RuntimeError("gate broke")),
    )
    monkeypatch.setattr(entrypoint, "_github_login_explicitly_bypassed", lambda: True)

    assert entrypoint._maybe_require_github_login(_console()) is True


def test_explicit_bypass_detects_skip_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENSRE_SKIP_GITHUB_LOGIN", "1")
    assert entrypoint._github_login_explicitly_bypassed() is True


def test_explicit_bypass_detects_ineligible_os(monkeypatch: Any) -> None:
    monkeypatch.delenv("OPENSRE_SKIP_GITHUB_LOGIN", raising=False)
    monkeypatch.setattr(entrypoint.platform, "system", lambda: "Linux")
    assert entrypoint._github_login_explicitly_bypassed() is True
