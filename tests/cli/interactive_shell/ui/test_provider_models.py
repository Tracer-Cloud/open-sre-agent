"""Tests for REPL provider/model resolution used by the welcome banner."""

from __future__ import annotations

from types import SimpleNamespace

from cli.interactive_shell.ui.provider import resolve_provider_models


def test_antigravity_cli_reads_model_env(monkeypatch: object) -> None:
    monkeypatch.setenv("ANTIGRAVITY_CLI_MODEL", "gemini-2.5-pro")

    reasoning, toolcall = resolve_provider_models(SimpleNamespace(), "antigravity-cli")

    assert reasoning == "gemini-2.5-pro"
    assert toolcall == "gemini-2.5-pro"


def test_antigravity_cli_falls_back_to_cli_default(monkeypatch: object) -> None:
    monkeypatch.delenv("ANTIGRAVITY_CLI_MODEL", raising=False)

    reasoning, toolcall = resolve_provider_models(SimpleNamespace(), "antigravity-cli")

    assert reasoning == "CLI default"
    assert toolcall == "CLI default"


def test_antigravity_cli_does_not_use_hyphenated_settings_attr() -> None:
    """Regression: hyphenated provider ids are not reachable via getattr(settings, ...)."""
    settings = SimpleNamespace(**{"antigravity-cli_model": "should-not-win"})

    reasoning, toolcall = resolve_provider_models(settings, "antigravity-cli")

    assert reasoning == "CLI default"
    assert toolcall == "CLI default"
