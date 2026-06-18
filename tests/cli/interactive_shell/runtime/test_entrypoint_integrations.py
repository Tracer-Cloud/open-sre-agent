"""Tests for hydrating configured integrations onto the REPL session at boot.

Without this the agent cannot answer "is X installed?" and the integration
guards stay dead because ``configured_integrations_known`` never flips to True.
"""

from __future__ import annotations

from typing import Any

from app.cli.interactive_shell.runtime import entrypoint
from app.cli.interactive_shell.runtime.session import ReplSession


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
