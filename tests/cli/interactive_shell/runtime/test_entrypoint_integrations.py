"""Tests for hydrating configured integrations onto the REPL session at boot.

Without this the agent cannot answer "is X installed?" and the integration
guards stay dead because ``configured_integrations_known`` never flips to True.
"""

from __future__ import annotations

from typing import Any

from app.cli.interactive_shell.runtime import entrypoint
from app.cli.interactive_shell.runtime.session import ReplSession


def test_hydrate_populates_session_from_catalog(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "app.integrations.catalog.configured_integration_services",
        lambda: ["gitlab", "datadog"],
    )
    session = ReplSession()
    entrypoint._hydrate_configured_integrations(session)
    assert session.configured_integrations_known is True
    assert session.configured_integrations == ("gitlab", "datadog")


def test_hydrate_marks_known_even_when_none_configured(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "app.integrations.catalog.configured_integration_services",
        list,
    )
    session = ReplSession()
    entrypoint._hydrate_configured_integrations(session)
    assert session.configured_integrations_known is True
    assert session.configured_integrations == ()


def test_hydrate_leaves_unknown_on_failure(monkeypatch: Any) -> None:
    def _boom() -> list[str]:
        raise RuntimeError("catalog blew up")

    monkeypatch.setattr(
        "app.integrations.catalog.configured_integration_services",
        _boom,
    )
    session = ReplSession()
    entrypoint._hydrate_configured_integrations(session)
    assert session.configured_integrations_known is False
    assert session.configured_integrations == ()
