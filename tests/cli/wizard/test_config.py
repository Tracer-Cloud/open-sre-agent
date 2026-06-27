"""Regression tests for wizard path constants."""

from __future__ import annotations

from cli.wizard.config import PROJECT_ENV_PATH, PROJECT_ROOT, SUPPORTED_PROVIDERS


def test_project_env_path_defaults_to_repo_root() -> None:
    """PROJECT_ROOT must resolve to the checkout root, not its parent directory."""
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert PROJECT_ENV_PATH == PROJECT_ROOT / ".env"


def test_provider_catalog_shows_auth_method_alternatives_together() -> None:
    """Onboarding should expose API-key and CLI choices where both exist."""
    labels_by_value = {provider.value: provider.label for provider in SUPPORTED_PROVIDERS}
    values = [provider.value for provider in SUPPORTED_PROVIDERS]

    assert labels_by_value["anthropic"] == "Anthropic API key"
    assert labels_by_value["claude-code"] == "Anthropic Claude Code CLI"
    assert values.index("anthropic") < values.index("claude-code") < values.index("openai")

    assert labels_by_value["openai"] == "OpenAI API key"
    assert labels_by_value["codex"] == "OpenAI Codex CLI"
    assert values.index("openai") < values.index("codex") < values.index("openrouter")

    assert labels_by_value["gemini"] == "Google Gemini API key"
    assert labels_by_value["gemini-cli"] == "Google Gemini CLI"
    assert labels_by_value["antigravity-cli"] == "Google Antigravity CLI"
    assert (
        values.index("gemini")
        < values.index("gemini-cli")
        < values.index("antigravity-cli")
        < values.index("nvidia")
    )

    assert labels_by_value["groq"] == "Groq API key"
    assert labels_by_value["grok-cli"] == "xAI Grok Build CLI"
    assert values.index("groq") < values.index("grok-cli") < values.index("cursor")
