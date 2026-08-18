from __future__ import annotations

from surfaces.cli.wizard.flow import _onboarding_provider_options


def test_onboarding_provider_options_hide_openai_anthropic_oauth_backends() -> None:
    """Onboarding presents OpenAI/Anthropic auth methods under the provider."""
    values = [provider.value for provider in _onboarding_provider_options()]

    assert "anthropic" in values
    assert "openai" in values
    assert "claude-code" not in values
    assert "codex" not in values
