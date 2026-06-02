from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import LLMSettings, has_credentials_for_active_llm_provider, resolve_llm_settings


def test_llm_settings_reject_provider_typos_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="Did you mean 'openai'"):
        LLMSettings.model_validate(
            {
                "provider": "opneai",
                "openai_api_key": "sk-test",
            }
        )


def test_llm_settings_require_api_key_for_selected_provider() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        LLMSettings.model_validate({"provider": "openai"})


def test_llm_settings_from_env_uses_secure_local_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "stored-secret" if env_var == "OPENAI_API_KEY" else "",
    )

    settings = LLMSettings.from_env()

    assert settings.provider == "openai"
    assert settings.openai_api_key == "stored-secret"


def test_llm_settings_require_minimax_api_key() -> None:
    with pytest.raises(ValidationError, match="MINIMAX_API_KEY"):
        LLMSettings.model_validate({"provider": "minimax"})


def test_llm_settings_require_deepseek_api_key() -> None:
    with pytest.raises(ValidationError, match="DEEPSEEK_API_KEY"):
        LLMSettings.model_validate({"provider": "deepseek"})


def test_llm_settings_deepseek_provider_accepted() -> None:
    settings = LLMSettings.model_validate(
        {
            "provider": "deepseek",
            "deepseek_api_key": "ds-test-key",
        }
    )
    assert settings.provider == "deepseek"
    assert settings.deepseek_api_key == "ds-test-key"
    assert settings.deepseek_reasoning_model == "deepseek-v4-pro"
    assert settings.deepseek_classification_model == "deepseek-v4-flash"
    assert settings.deepseek_toolcall_model == "deepseek-v4-flash"


def test_llm_settings_from_env_deepseek(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "ds-stored-key" if env_var == "DEEPSEEK_API_KEY" else "",
    )

    settings = LLMSettings.from_env()

    assert settings.provider == "deepseek"
    assert settings.deepseek_api_key == "ds-stored-key"


def test_llm_settings_minimax_provider_accepted() -> None:
    settings = LLMSettings.model_validate(
        {
            "provider": "minimax",
            "minimax_api_key": "mm-test-key",
        }
    )
    assert settings.provider == "minimax"
    assert settings.minimax_api_key == "mm-test-key"
    assert settings.minimax_reasoning_model == "MiniMax-M2.7"
    assert settings.minimax_toolcall_model == "MiniMax-M2.7-highspeed"


def test_llm_settings_from_env_minimax(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "mm-stored-key" if env_var == "MINIMAX_API_KEY" else "",
    )

    settings = LLMSettings.from_env()

    assert settings.provider == "minimax"
    assert settings.minimax_api_key == "mm-stored-key"


def test_llm_settings_custom_openai_provider_accepted() -> None:
    settings = LLMSettings.model_validate(
        {
            "provider": "custom-openai",
            "custom_openai_api_key": "sk-custom",
            "custom_openai_base_url": "http://localhost:4000/v1",
        }
    )
    assert settings.provider == "custom-openai"
    assert settings.custom_openai_base_url == "http://localhost:4000/v1"
    # Tier defaults mirror the OpenAI tier defaults when unset.
    assert settings.custom_openai_reasoning_model == "gpt-5.4-mini"


def test_llm_settings_custom_anthropic_provider_accepted() -> None:
    settings = LLMSettings.model_validate(
        {
            "provider": "custom-anthropic",
            "custom_anthropic_api_key": "sk-ant-custom",
            "custom_anthropic_base_url": "https://proxy.example.com",
        }
    )
    assert settings.provider == "custom-anthropic"
    assert settings.custom_anthropic_base_url == "https://proxy.example.com"
    assert settings.custom_anthropic_reasoning_model == "claude-opus-4-7"


def test_llm_settings_require_custom_openai_api_key() -> None:
    with pytest.raises(ValidationError, match="CUSTOM_OPENAI_API_KEY"):
        LLMSettings.model_validate(
            {"provider": "custom-openai", "custom_openai_base_url": "http://localhost:4000/v1"}
        )


def test_llm_settings_require_custom_openai_base_url() -> None:
    with pytest.raises(ValidationError, match="CUSTOM_OPENAI_BASE_URL"):
        LLMSettings.model_validate(
            {"provider": "custom-openai", "custom_openai_api_key": "sk-custom"}
        )


def test_llm_settings_require_custom_anthropic_base_url() -> None:
    with pytest.raises(ValidationError, match="CUSTOM_ANTHROPIC_BASE_URL"):
        LLMSettings.model_validate(
            {"provider": "custom-anthropic", "custom_anthropic_api_key": "sk-ant-custom"}
        )


def test_llm_settings_from_env_custom_openai_per_tier_overrides(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "custom-openai")
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://gateway:4000/v1")
    # CUSTOM_OPENAI_MODEL is the single-model fallback; the per-tier var wins for its tier.
    monkeypatch.setenv("CUSTOM_OPENAI_MODEL", "shared-model")
    monkeypatch.setenv("CUSTOM_OPENAI_REASONING_MODEL", "reasoner-model")
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "sk-stored" if env_var == "CUSTOM_OPENAI_API_KEY" else "",
    )

    settings = LLMSettings.from_env()

    assert settings.provider == "custom-openai"
    assert settings.custom_openai_api_key == "sk-stored"
    assert settings.custom_openai_base_url == "http://gateway:4000/v1"
    assert settings.custom_openai_reasoning_model == "reasoner-model"
    assert settings.custom_openai_classification_model == "shared-model"
    assert settings.custom_openai_toolcall_model == "shared-model"


@pytest.mark.parametrize(
    "raw_host, expected",
    [
        ("localhost:11434", "http://localhost:11434"),
        ("192.168.1.5:11434", "http://192.168.1.5:11434"),
        ("my-server:11434", "http://my-server:11434"),
        ("http://localhost:11434", "http://localhost:11434"),
        ("https://ollama.internal", "https://ollama.internal"),
    ],
)
def test_llm_settings_ollama_host_protocol_normalized(raw_host: str, expected: str) -> None:
    settings = LLMSettings.model_validate({"provider": "ollama", "ollama_host": raw_host})
    assert settings.ollama_host == expected


def test_llm_settings_from_env_max_tokens_override(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    settings = LLMSettings.from_env()

    assert settings.max_tokens == 8192


def test_llm_settings_from_env_max_tokens_invalid_raises(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MAX_TOKENS", "not-a-number")
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    with pytest.raises((ValueError, ValidationError)):
        LLMSettings.from_env()


def test_llm_settings_from_env_max_tokens_default(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    from app.config import DEFAULT_MAX_TOKENS

    settings = LLMSettings.from_env()

    assert settings.max_tokens == DEFAULT_MAX_TOKENS


def test_llm_settings_from_env_claude_code_without_api_key(monkeypatch) -> None:
    """CLI-backed Claude Code: onboard writes LLM_PROVIDER only; no hosted API key."""
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    settings = LLMSettings.from_env()

    assert settings.provider == "claude-code"


def test_llm_settings_from_env_gemini_cli_without_api_key(monkeypatch) -> None:
    """CLI-backed Gemini CLI provider should not require GEMINI_API_KEY in config validation."""
    monkeypatch.setenv("LLM_PROVIDER", "gemini-cli")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    settings = LLMSettings.from_env()

    assert settings.provider == "gemini-cli"


def test_llm_settings_from_env_copilot_without_api_key(monkeypatch) -> None:
    """CLI-backed Copilot CLI: vendor auth, no hosted API key required."""
    monkeypatch.setenv("LLM_PROVIDER", "copilot")
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    settings = LLMSettings.from_env()

    assert settings.provider == "copilot"


def test_llm_settings_copilot_provider_accepted() -> None:
    settings = LLMSettings.model_validate({"provider": "copilot"})
    assert settings.provider == "copilot"


def test_has_credentials_for_active_llm_provider_missing_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    assert has_credentials_for_active_llm_provider() is False


def test_resolve_llm_settings_falls_back_to_openai_when_default_anthropic_key_missing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "sk-openai" if env_var == "OPENAI_API_KEY" else "",
    )

    settings = resolve_llm_settings()

    assert settings.provider == "openai"
    assert settings.openai_api_key == "sk-openai"
    assert has_credentials_for_active_llm_provider() is True


def test_has_credentials_for_active_llm_provider_with_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "sk-x" if env_var == "OPENAI_API_KEY" else "",
    )

    assert has_credentials_for_active_llm_provider() is True


def test_has_credentials_for_active_llm_provider_ollama_never_requires_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    assert has_credentials_for_active_llm_provider() is True


def test_has_credentials_for_active_llm_provider_copilot_never_requires_key(monkeypatch) -> None:
    """CLI-backed Copilot must never require a hosted API key, same as Ollama / other CLIs."""
    monkeypatch.setenv("LLM_PROVIDER", "copilot")
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    assert has_credentials_for_active_llm_provider() is True


def test_has_credentials_for_active_llm_provider_re_raises_non_key_validation_errors(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MAX_TOKENS", "0")
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "sk" if env_var == "OPENAI_API_KEY" else "",
    )

    with pytest.raises(ValidationError, match="greater than 0"):
        has_credentials_for_active_llm_provider()


def test_has_credentials_for_active_llm_provider_re_raises_invalid_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    with pytest.raises(ValidationError, match="Unsupported LLM provider"):
        has_credentials_for_active_llm_provider()
