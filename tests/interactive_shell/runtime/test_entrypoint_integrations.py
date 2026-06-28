"""Tests for hydrating configured integrations onto the REPL session at boot.

Without this the agent cannot answer "is X installed?" and the integration
guards stay dead because ``configured_integrations_known`` never flips to True.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

from rich.console import Console

import interactive_shell.main as main_entrypoint
from core.agent_harness.session import ReplSession
from interactive_shell.runtime.startup import first_launch_github as flg


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def test_hydrate_populates_session_from_effective_resolution(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "integrations.verify.resolve_effective_integrations",
        lambda: {"gitlab": {}, "datadog": {}},
    )
    session = ReplSession()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is True
    # Resolution covers env + local store and is returned in sorted order.
    assert session.configured_integrations == ("datadog", "gitlab")


def test_hydrate_marks_known_even_when_none_configured(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "integrations.verify.resolve_effective_integrations",
        dict,
    )
    session = ReplSession()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is True
    assert session.configured_integrations == ()


def test_warm_resolved_integrations_populates_cache(monkeypatch: Any) -> None:
    resolved = {"datadog": {"site": "datadoghq.com"}, "grafana": {"url": "http://localhost"}}
    monkeypatch.setattr(
        "tools.investigation.stages.resolve_integrations.resolve_integrations_quiet",
        lambda _state: resolved,
    )
    session = ReplSession()
    session.warm_resolved_integrations()
    assert session.resolved_integrations_cache == resolved


def test_warm_resolved_integrations_is_idempotent(monkeypatch: Any) -> None:
    calls: list[str] = []

    def _resolve(_state: dict[str, Any]) -> dict[str, Any]:
        calls.append("resolve")
        return {"github": {}}

    monkeypatch.setattr(
        "tools.investigation.stages.resolve_integrations.resolve_integrations_quiet",
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
        return {}

    monkeypatch.setattr(
        "tools.investigation.stages.resolve_integrations.resolve_integrations_quiet",
        _resolve,
    )
    session = ReplSession()
    session.warm_resolved_integrations()
    assert session.resolved_integrations_cache is None
    session.warm_resolved_integrations()
    assert calls == ["resolve", "resolve"]


def test_warm_resolved_integrations_uses_quiet_resolve(monkeypatch: Any) -> None:
    progress_calls: list[str] = []
    quiet_calls: list[str] = []

    monkeypatch.setattr(
        "tools.investigation.stages.resolve_integrations.resolve_integrations",
        lambda _state: progress_calls.append("progress") or {"resolved_integrations": {}},
    )
    monkeypatch.setattr(
        "tools.investigation.stages.resolve_integrations.resolve_integrations_quiet",
        lambda _state: quiet_calls.append("quiet") or {"datadog": {}},
    )

    session = ReplSession()
    session.warm_resolved_integrations()

    assert quiet_calls == ["quiet"]
    assert progress_calls == []
    assert session.resolved_integrations_cache == {"datadog": {}}


def test_stale_background_warm_does_not_overwrite_refreshed_cache() -> None:
    session = ReplSession()
    stale_generation = session._integration_warm_generation
    session._integration_warm_generation += 1
    session._store_warm_cache(
        {"fresh": {"token": "new"}}, generation=session._integration_warm_generation
    )
    session._store_warm_cache({"stale": {"token": "old"}}, generation=stale_generation)
    assert session.resolved_integrations_cache == {"fresh": {"token": "new"}}


def test_hydrate_entrypoint_does_not_warm_before_prompt(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "integrations.verify.resolve_effective_integrations",
        lambda: {"datadog": {}},
    )
    resolve_calls: list[str] = []

    def _resolve(_state: dict[str, Any]) -> dict[str, Any]:
        resolve_calls.append("resolve")
        return {"datadog": {"site": "datadoghq.com"}}

    monkeypatch.setattr(
        "tools.investigation.stages.resolve_integrations.resolve_integrations_quiet",
        _resolve,
    )
    session = ReplSession()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is True
    assert session.resolved_integrations_cache is None
    assert resolve_calls == []


def test_schedule_warm_resolved_integrations_runs_in_background(
    monkeypatch: Any,
) -> None:
    import asyncio

    warmed = asyncio.Event()

    def _warm(self: ReplSession, *, generation: int | None = None) -> None:
        warmed.set()

    monkeypatch.setattr(ReplSession, "warm_resolved_integrations", _warm)

    async def _run() -> None:
        session = ReplSession()
        session.schedule_warm_resolved_integrations()
        await asyncio.wait_for(warmed.wait(), timeout=1.0)
        assert warmed.is_set()

    asyncio.run(_run())


def test_hydrate_leaves_unknown_on_failure(monkeypatch: Any) -> None:
    def _boom() -> dict[str, Any]:
        raise RuntimeError("catalog blew up")

    monkeypatch.setattr(
        "integrations.verify.resolve_effective_integrations",
        _boom,
    )
    session = ReplSession()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is False
    assert session.configured_integrations == ()


def test_gate_error_blocks_startup_without_bypass(monkeypatch: Any) -> None:
    """On an unexpected gate error we must NOT fail open into the REPL unless an
    explicit bypass applies."""
    monkeypatch.setattr(
        flg,
        "should_require_github_login",
        lambda: (_ for _ in ()).throw(RuntimeError("gate broke")),
    )
    monkeypatch.setattr(flg, "_github_login_explicitly_bypassed", lambda: False)

    assert flg.require_startup_github_login(_console()) is False


def test_gate_error_allows_startup_with_bypass(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        flg,
        "should_require_github_login",
        lambda: (_ for _ in ()).throw(RuntimeError("gate broke")),
    )
    monkeypatch.setattr(flg, "_github_login_explicitly_bypassed", lambda: True)

    assert flg.require_startup_github_login(_console()) is True


def test_repl_main_identifies_saved_github_username(monkeypatch: Any) -> None:
    identified: list[str] = []
    monkeypatch.setattr(
        "platform.analytics.cli.identify_saved_github_username",
        lambda: identified.append("called"),
    )

    def _run_initial_input(*_args: Any, **_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(main_entrypoint, "run_initial_input", _run_initial_input)

    class _Session:
        active_theme_name = None

        def hydrate_configured_integrations(self) -> None:
            return None

        def warm_resolved_integrations(self) -> None:
            return None

    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime_context",
        lambda **_kwargs: SimpleNamespace(session=_Session(), inbox=None),
    )

    class _PromptSession:
        history = None

    def _build_prompt_session() -> _PromptSession:
        return _PromptSession()

    monkeypatch.setattr(
        main_entrypoint._input_prompt,
        "_build_prompt_session",
        _build_prompt_session,
    )

    import asyncio

    asyncio.run(main_entrypoint.repl_main(initial_input="hello"))

    assert identified == ["called"]


def test_explicit_bypass_detects_skip_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENSRE_SKIP_GITHUB_LOGIN", "1")
    assert flg._github_login_explicitly_bypassed() is True


def test_explicit_bypass_detects_ci_environment(monkeypatch: Any) -> None:
    monkeypatch.delenv("OPENSRE_SKIP_GITHUB_LOGIN", raising=False)
    monkeypatch.setenv("CI", "true")
    assert flg._github_login_explicitly_bypassed() is True
