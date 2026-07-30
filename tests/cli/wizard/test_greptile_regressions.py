from __future__ import annotations

from config.config import Environment
from surfaces.cli.wizard.config import SUPPORTED_PROVIDERS
from surfaces.cli.wizard.env_sync import sync_provider_env


def _provider(value: str):
    return next(provider for provider in SUPPORTED_PROVIDERS if provider.value == value)


def test_environment_members_keep_string_contract() -> None:
    assert isinstance(Environment.DEVELOPMENT, str)
    assert str(Environment.DEVELOPMENT) == "development"


def test_fresh_custom_provider_persists_all_model_tiers(tmp_path, monkeypatch) -> None:
    provider = _provider("custom-openai")
    assert provider.endpoint_env is not None
    monkeypatch.setenv(provider.endpoint_env, "https://gateway.example/v1")
    env_path = tmp_path / ".env"

    sync_provider_env(provider=provider, model="gateway-model", env_path=env_path)

    saved = env_path.read_text(encoding="utf-8")
    assert "CUSTOM_OPENAI_REASONING_MODEL=gateway-model" in saved
    assert "CUSTOM_OPENAI_CLASSIFICATION_MODEL=gateway-model" in saved
    assert "CUSTOM_OPENAI_TOOLCALL_MODEL=gateway-model" in saved
