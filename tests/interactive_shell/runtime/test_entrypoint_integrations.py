"""Tests for hydrating configured integrations onto the REPL session at boot.

Without this the agent cannot answer "is X installed?" and the integration
guards stay dead because ``configured_integrations_known`` never flips to True.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rich.console import Console

import surfaces.interactive_shell.main as main_entrypoint
from core.agent_harness.session.integration_resolution import IntegrationResolutionResult
from surfaces.interactive_shell.runtime.startup import first_launch_github as flg
from surfaces.interactive_shell.session import Session


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def test_hydrate_populates_session_from_effective_resolution(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "platform.harness_ports.configured_integration_services",
        lambda: ["gitlab", "datadog"],
    )
    session = Session()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is True
    # Metadata discovery covers env + local store and is returned in sorted order.
    assert session.configured_integrations == ("datadog", "gitlab")


def test_hydrate_marks_known_even_when_none_configured(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "platform.harness_ports.configured_integration_services",
        list,
    )
    session = Session()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is True
    assert session.configured_integrations == ()


def test_warm_resolved_integrations_populates_cache(monkeypatch: Any) -> None:
    resolved = {"datadog": {"site": "datadoghq.com"}, "grafana": {"url": "http://localhost"}}
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: resolved,
    )
    session = Session()
    session.warm_resolved_integrations()
    assert session.resolved_integrations_cache == resolved


def test_warm_resolved_integrations_is_idempotent(monkeypatch: Any) -> None:
    calls: list[str] = []

    def _resolve() -> dict[str, Any]:
        calls.append("resolve")
        return {"github": {}}

    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        _resolve,
    )
    session = Session()
    session.warm_resolved_integrations()
    session.warm_resolved_integrations()
    assert calls == ["resolve"]


def test_warm_resolved_integrations_skips_empty_cache(monkeypatch: Any) -> None:
    calls: list[str] = []

    def _resolve() -> dict[str, Any]:
        calls.append("resolve")
        return {}

    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        _resolve,
    )
    session = Session()
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
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: quiet_calls.append("quiet") or {"datadog": {}},
    )

    session = Session()
    session.warm_resolved_integrations()

    assert quiet_calls == ["quiet"]
    assert progress_calls == []
    assert session.resolved_integrations_cache == {"datadog": {}}


def test_get_integrations_returns_pydantic_cached_result(monkeypatch: Any) -> None:
    def _unexpected_resolve() -> dict[str, Any]:
        raise AssertionError("cached integrations should not re-resolve")

    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        _unexpected_resolve,
    )
    session = Session()
    session.resolved_integrations_cache = {"datadog": {"site": "datadoghq.com"}}

    result = session.get_integrations()

    assert isinstance(result, IntegrationResolutionResult)
    assert result.resolved_integrations == {"datadog": {"site": "datadoghq.com"}}
    assert result.services == ("datadog",)


def test_get_integrations_respects_explicit_empty_cache(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: calls.append("resolve") or {"datadog": {}},
    )
    session = Session()
    session.resolved_integrations_cache = {}

    result = session.get_integrations()

    assert result.resolved_integrations == {}
    assert calls == []


def test_get_integrations_warms_metadata_only_cache(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: calls.append("resolve") or {"datadog": {"site": "datadoghq.com"}},
    )
    session = Session()
    session.resolved_integrations_cache = {"_gateway_chat_id": "chat-1"}

    result = session.get_integrations()

    assert calls == ["resolve"]
    assert result.resolved_integrations == {
        "_gateway_chat_id": "chat-1",
        "datadog": {"site": "datadoghq.com"},
    }


def test_stale_background_warm_does_not_overwrite_refreshed_cache() -> None:
    session = Session()
    stale_generation = session.integrations._warm_generation
    session.integrations._warm_generation += 1
    session.integrations._store(
        {"fresh": {"token": "new"}}, generation=session.integrations._warm_generation
    )
    session.integrations._store({"stale": {"token": "old"}}, generation=stale_generation)
    assert session.resolved_integrations_cache == {"fresh": {"token": "new"}}


def test_hydrate_entrypoint_does_not_warm_before_prompt(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "platform.harness_ports.configured_integration_services",
        lambda: ["datadog"],
    )
    resolve_calls: list[str] = []

    def _resolve() -> dict[str, Any]:
        resolve_calls.append("resolve")
        return {"datadog": {"site": "datadoghq.com"}}

    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        _resolve,
    )
    session = Session()
    session.hydrate_configured_integrations()
    assert session.configured_integrations_known is True
    assert session.resolved_integrations_cache is None
    assert resolve_calls == []


def test_hydrate_leaves_unknown_on_failure(monkeypatch: Any) -> None:
    def _boom() -> list[str]:
        raise RuntimeError("catalog blew up")

    monkeypatch.setattr(
        "platform.harness_ports.configured_integration_services",
        _boom,
    )
    session = Session()
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


def test_run_repl_async_identifies_saved_github_username(monkeypatch: Any) -> None:
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

    monkeypatch.setattr(
        main_entrypoint,
        "build_prompt_session",
        lambda: _PromptSession(),
    )

    import asyncio

    asyncio.run(main_entrypoint.run_repl_async(initial_input="hello"))

    assert identified == ["called"]


def test_run_repl_async_failed_resume_flushes_starter_session(
    monkeypatch: Any, tmp_path: Path
) -> None:
    import asyncio

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr(
        "platform.analytics.cli.identify_saved_github_username",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.session_cmds.resume.resume_session_by_prefix",
        lambda *_args, **_kwargs: False,
    )

    session = Session()
    flushed: list[str] = []
    original_flush = session.storage.flush

    def _track_flush(current_session: Session) -> None:
        flushed.append(current_session.session_id)
        original_flush(current_session)

    monkeypatch.setattr(session.storage, "flush", _track_flush)

    class _PromptSession:
        history = None

    monkeypatch.setattr(
        main_entrypoint,
        "build_prompt_session",
        lambda: _PromptSession(),
    )
    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime_context",
        lambda **_kwargs: SimpleNamespace(session=session, inbox=None),
    )

    exit_code = asyncio.run(main_entrypoint.run_repl_async(resume_session_id="missing-session"))

    assert exit_code == 1
    assert flushed == [session.session_id]
    assert not (sessions_dir / f"{session.session_id}.jsonl").exists()


def test_explicit_bypass_detects_skip_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENSRE_SKIP_GITHUB_LOGIN", "1")
    assert flg._github_login_explicitly_bypassed() is True


def test_explicit_bypass_detects_ci_environment(monkeypatch: Any) -> None:
    monkeypatch.delenv("OPENSRE_SKIP_GITHUB_LOGIN", raising=False)
    monkeypatch.setenv("CI", "true")
    assert flg._github_login_explicitly_bypassed() is True
