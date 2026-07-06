from __future__ import annotations

from pathlib import Path

import keyring
import keyring.errors
import pytest
from keyring.backend import KeyringBackend

from config.llm_auth.credentials import resolve_for_request, status
from config.llm_auth.records import resolve_provider_auth_record, save_provider_auth_record
from config.llm_credentials import resolve_llm_api_key
from integrations.llm_cli.base import CLIProbe
from integrations.llm_cli.codex_oauth import CodexOAuthResult
from surfaces.cli.llm_auth.providers import ProviderAuthProfile, resolve_auth_profile
from surfaces.cli.llm_auth.service import (
    AuthSetupError,
    configure_api_key_provider,
    configure_cli_subscription_provider,
)
from surfaces.cli.wizard.config import ModelOption, ProviderOption
from surfaces.cli.wizard.validation import ValidationResult
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
        "surfaces.cli.wizard.store.get_store_path",
        lambda: tmp_path / "opensre.json",
    )
    monkeypatch.setattr(
        "surfaces.cli.llm_auth.service.validate_provider_credentials",
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
        "surfaces.cli.llm_auth.service.validate_provider_credentials",
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


class _InconclusiveCodexAdapter:
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
            logged_in=None,
            bin_path="/usr/bin/fake",
            detail="Login status unknown.",
        )


def test_configure_cli_subscription_syncs_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(
        "surfaces.cli.wizard.store.get_store_path",
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
    monkeypatch.setattr(
        "surfaces.cli.llm_auth.service.provider_for_profile", lambda _profile: fake_provider
    )

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


def test_configure_cli_subscription_uses_managed_codex_oauth_when_status_probe_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(
        "surfaces.cli.wizard.store.get_store_path",
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
        adapter_factory=_InconclusiveCodexAdapter,
        allow_custom_models=True,
    )
    monkeypatch.setattr(
        "surfaces.cli.llm_auth.service.provider_for_profile", lambda _profile: fake_provider
    )
    oauth_calls: list[object] = []

    def _fake_codex_oauth_login() -> CodexOAuthResult:
        oauth_calls.append(None)
        return CodexOAuthResult(
            account_id="account-123",
            auth_path=tmp_path / "codex-home" / "auth.json",
            detail="OpenAI OAuth tokens stored for Codex.",
        )

    monkeypatch.setattr(
        "surfaces.cli.llm_auth.service.run_codex_oauth_login", _fake_codex_oauth_login
    )

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

        assert oauth_calls == [None]
        assert result.provider == "codex"
        assert result.source == "codex-oauth"
        assert result.detail == "OpenAI OAuth tokens stored for Codex."
        env_content = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=openai\n" in env_content
        assert "LLM_AUTH_METHOD=oauth\n" in env_content
        assert "CODEX_MODEL=gpt-5-codex\n" in env_content
        assert resolve_provider_auth_record("codex")["source"] == "codex-oauth"
    finally:
        keyring.set_keyring(previous_backend)


class _ErroringKeyring(KeyringBackend):
    """Simulates a keychain backend that can't be reached (e.g. no D-Bus session)."""

    priority = 1.0

    def get_password(self, _service: str, _username: str) -> str | None:
        raise keyring.errors.KeyringError("Secret Service is not reachable")

    def set_password(self, _service: str, _username: str, _password: str) -> None:
        raise keyring.errors.KeyringError("Secret Service is not reachable")

    def delete_password(self, _service: str, _username: str) -> None:
        raise keyring.errors.KeyringError("Secret Service is not reachable")


def test_resolve_for_request_does_not_stale_verified_record_on_keyring_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend hiccup (regression for #3721) must not downgrade a verified credential.

    Previously any ``keyring.errors.KeyringError`` during the on-demand keychain
    read was swallowed into an empty string, which read as "credential missing"
    and permanently marked the metadata record stale/unverified — even though
    the secret itself was untouched.
    """
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # OPENSRE_LLM_AUTH_METADATA_PATH is already redirected to this same tmp_path
    # by the autouse ``_isolate_opensre_home_files`` fixture in tests/conftest.py.
    save_provider_auth_record(
        provider="deepseek",
        auth_name="deepseek",
        kind="api_key",
        source="keyring",
        detail="DEEPSEEK_API_KEY stored in the system keychain.",
        verified=True,
        stale=False,
        env_var="DEEPSEEK_API_KEY",
    )

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(_ErroringKeyring())
    try:
        resolution = resolve_for_request("deepseek")

        assert resolution.ok is False
        assert "keychain" in resolution.detail.lower()

        record = resolve_provider_auth_record("deepseek")
        assert record["stale"] == "false"
        assert record["verified"] == "true"
        assert status("deepseek").verified is True
    finally:
        keyring.set_keyring(previous_backend)


def test_resolve_for_request_still_stales_when_credential_is_genuinely_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean (error-free) keychain miss is real evidence and should still stale."""
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # OPENSRE_LLM_AUTH_METADATA_PATH is already redirected by the autouse
    # ``_isolate_opensre_home_files`` fixture in tests/conftest.py.
    save_provider_auth_record(
        provider="deepseek",
        auth_name="deepseek",
        kind="api_key",
        source="keyring",
        detail="DEEPSEEK_API_KEY stored in the system keychain.",
        verified=True,
        stale=False,
        env_var="DEEPSEEK_API_KEY",
    )

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        resolution = resolve_for_request("deepseek")

        assert resolution.ok is False
        record = resolve_provider_auth_record("deepseek")
        assert record["stale"] == "true"
        assert record["verified"] == "false"
    finally:
        keyring.set_keyring(previous_backend)


def test_codex_oauth_metadata_counts_as_prompt_safe_cli_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    save_provider_auth_record(
        provider="codex",
        auth_name="chatgpt",
        kind="cli_subscription",
        source="codex-oauth",
        detail="OpenAI OAuth tokens stored for Codex.",
    )

    from config.llm_auth.credentials import status

    result = status("codex")

    assert result.configured is True
    assert result.source == "cli"
    assert result.detail == "OpenAI OAuth tokens stored for Codex."
