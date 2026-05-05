from __future__ import annotations

from unittest.mock import MagicMock

from app.cli.commands import doctor


def test_check_llm_provider_not_set(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    ok, detail = doctor._check_llm_provider()
    assert ok is False
    assert "not set" in detail


def test_check_llm_provider_hosted_missing_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ok, detail = doctor._check_llm_provider()
    assert ok is False
    assert "ANTHROPIC_API_KEY" in detail


def test_check_llm_provider_claude_code_ready(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    reg = MagicMock()
    reg.adapter_factory.return_value.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/claude",
        logged_in=True,
        detail="Authenticated via Claude subscription.",
    )
    monkeypatch.setattr(
        "app.integrations.llm_cli.registry.get_cli_provider_registration",
        lambda provider: reg if provider == "claude-code" else None,
    )
    ok, detail = doctor._check_llm_provider()
    assert ok is True
    assert "CLI ready" in detail


def test_check_llm_provider_claude_code_auth_unclear(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    reg = MagicMock()
    reg.adapter_factory.return_value.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/claude",
        logged_in=None,
        detail="claude auth status failed: unknown command",
    )
    monkeypatch.setattr(
        "app.integrations.llm_cli.registry.get_cli_provider_registration",
        lambda provider: reg if provider == "claude-code" else None,
    )
    ok, detail = doctor._check_llm_provider()
    assert ok is False
    assert "auth status unclear" in detail


def test_check_llm_provider_cli_branch_follows_registry_not_hardcoded_ids(monkeypatch) -> None:
    """Any LLM_PROVIDER listed in CLI_PROVIDER_REGISTRY gets the CLI probe path."""
    monkeypatch.setenv("LLM_PROVIDER", "hypothetical-cli")
    reg = MagicMock()
    reg.adapter_factory.return_value.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/hypothetical",
        logged_in=True,
        detail="CLI OK.",
    )
    monkeypatch.setattr(
        "app.integrations.llm_cli.registry.get_cli_provider_registration",
        lambda provider: reg if provider == "hypothetical-cli" else None,
    )
    ok, detail = doctor._check_llm_provider()
    assert ok is True
    assert "CLI ready" in detail
