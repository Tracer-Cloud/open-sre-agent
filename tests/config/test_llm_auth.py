from __future__ import annotations

from pathlib import Path

import keyring
import pytest

from cli.llm_auth.providers import ProviderAuthProfile, resolve_auth_profile
from cli.llm_auth.service import (
    AuthSetupError,
    configure_api_key_provider,
    configure_cli_subscription_provider,
)
from cli.wizard.config import ModelOption, ProviderOption
from cli.wizard.validation import ValidationResult
from config.llm_auth.records import resolve_provider_auth_record
from config.llm_credentials import resolve_llm_api_key
from integrations.llm_cli.base import CLIProbe
from tests.shared.keyring_backend import MemoryKeyring


def test_resolve_auth_profile_accepts_subscription_aliases() -> None:
    assert resolve_auth_profile("chatgpt").provider_value == "codex"
    assert resolve_auth_profile("openai-codex").provider_value == "codex"
    assert resolve_auth_profile("claude").provider_value == "claude-code"
    assert resolve_auth_profile("deepseek").provider_value == "deepseek"


def test_configure_deepseek_api_key_stores_keyring_and_nonsecret_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(
        "cli.wizard.store.get_store_path",
        lambda: tmp_path / "opensre.json",
    )
    monkeypatch.setattr(
        "cli.llm_auth.service.validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="ok"),
    )

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        env_path = tmp_path / ".env"
        result = configure_api_key_provider(
            profile=resolve_auth_profile("deepseek"),
            api_key="deepseek-secret",
            model="deepseek-v4-flash",
            env_path=env_path,
        )

        assert result.provider == "deepseek"
        assert resolve_llm_api_key("DEEPSEEK_API_KEY") == "deepseek-secret"
        env_content = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=deepseek\n" in env_content
        assert "DEEPSEEK_REASONING_MODEL=deepseek-v4-flash\n" in env_content
        assert "DEEPSEEK_API_KEY=" not in env_content
        assert resolve_provider_auth_record("deepseek")["source"] == "keyring"
    finally:
        keyring.set_keyring(previous_backend)


def test_configure_api_key_does_not_store_when_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(
        "cli.llm_auth.service.validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=False, detail="rejected"),
    )

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        with pytest.raises(AuthSetupError, match="rejected"):
            configure_api_key_provider(
                profile=resolve_auth_profile("deepseek"),
                api_key="bad-key",
            )
        assert resolve_llm_api_key("DEEPSEEK_API_KEY") == ""
    finally:
        keyring.set_keyring(previous_backend)


class _FakeAdapter:
    name = "fake"
    binary_env_key = "FAKE_BIN"
    install_hint = "install fake"
    auth_hint = "Run: fake login"
    min_version = None
    default_exec_timeout_sec = 30.0

    def detect(self) -> CLIProbe:
        return CLIProbe(
            installed=True,
            version="1.0.0",
            logged_in=True,
            bin_path="/usr/bin/fake",
            detail="Logged in.",
        )


def test_configure_cli_subscription_syncs_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(
        "cli.wizard.store.get_store_path",
        lambda: tmp_path / "opensre.json",
    )
    fake_provider = ProviderOption(
        value="codex",
        label="OpenAI Codex CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="CODEX_MODEL",
        default_model="",
        models=(ModelOption(value="", label="default"),),
        credential_kind="cli",
        adapter_factory=_FakeAdapter,
        allow_custom_models=True,
    )
    monkeypatch.setattr("cli.llm_auth.service.provider_for_profile", lambda _profile: fake_provider)

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        env_path = tmp_path / ".env"
        result = configure_cli_subscription_provider(
            profile=ProviderAuthProfile(
                name="chatgpt",
                provider_value="codex",
                label="ChatGPT subscription via Codex CLI",
                kind="cli_subscription",
            ),
            model="gpt-5-codex",
            env_path=env_path,
        )

        assert result.provider == "codex"
        env_content = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=codex\n" in env_content
        assert "CODEX_MODEL=gpt-5-codex\n" in env_content
        assert resolve_provider_auth_record("codex")["source"] == "vendor-cli"
    finally:
        keyring.set_keyring(previous_backend)
