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
