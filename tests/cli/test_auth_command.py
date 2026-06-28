from __future__ import annotations

from pathlib import Path

import keyring
from click.testing import CliRunner

from cli.__main__ import cli
from cli.llm_auth.service import AuthSetupResult
from config.llm_credentials import resolve_llm_api_key
from tests.shared.keyring_backend import MemoryKeyring


def _patch_auth_env(monkeypatch, tmp_path: Path) -> Path:
    env_path = tmp_path / ".env"
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr("cli.wizard.env_sync.PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr("cli.wizard.store.get_store_path", lambda: tmp_path / "opensre.json")
    return env_path


def test_auth_login_deepseek_stores_keyring_not_env(monkeypatch, tmp_path: Path) -> None:
    env_path = _patch_auth_env(monkeypatch, tmp_path)

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        result = CliRunner().invoke(
            cli,
            [
                "auth",
                "login",
                "deepseek",
                "--api-key",
                "deepseek-secret",
                "--no-validate",
                "--no-open-browser",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Authenticated: DeepSeek API key" in result.output
        assert resolve_llm_api_key("DEEPSEEK_API_KEY") == "deepseek-secret"
        env_content = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=deepseek\n" in env_content
        assert "DEEPSEEK_API_KEY=" not in env_content
    finally:
        keyring.set_keyring(previous_backend)


def test_auth_status_provider_reports_metadata_without_keychain_verify(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_auth_env(monkeypatch, tmp_path)
    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        CliRunner().invoke(
            cli,
            [
                "auth",
                "login",
                "deepseek",
                "--api-key",
                "deepseek-secret",
                "--no-validate",
                "--no-open-browser",
            ],
        )

        result = CliRunner().invoke(cli, ["auth", "status", "deepseek"])

        assert result.exit_code == 0, result.output
        assert "deepseek" in result.output
        assert "ok" in result.output
        assert "metadata" in result.output
    finally:
        keyring.set_keyring(previous_backend)


def test_auth_verify_provider_reports_keyring_source(monkeypatch, tmp_path: Path) -> None:
    _patch_auth_env(monkeypatch, tmp_path)
    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        CliRunner().invoke(
            cli,
            [
                "auth",
                "login",
                "deepseek",
                "--api-key",
                "deepseek-secret",
                "--no-validate",
                "--no-open-browser",
            ],
        )

        result = CliRunner().invoke(cli, ["auth", "verify", "deepseek"])

        assert result.exit_code == 0, result.output
        assert "Provider : deepseek" in result.output
        assert "Status   : ok" in result.output
        assert "Source   : keyring" in result.output
        assert "secure local storage" in result.output
    finally:
        keyring.set_keyring(previous_backend)


def test_auth_login_chatgpt_delegates_to_subscription_provider(monkeypatch, tmp_path: Path) -> None:
    _patch_auth_env(monkeypatch, tmp_path)
    calls: list[tuple[str, bool]] = []

    def _fake_configure(**kwargs):
        calls.append((kwargs["profile"].name, kwargs["launch_login"]))
        return AuthSetupResult(
            provider="codex",
            model="",
            source="vendor-cli",
            detail="Logged in.",
            env_path=tmp_path / ".env",
        )

    monkeypatch.setattr("cli.commands.auth.configure_cli_subscription_provider", _fake_configure)

    result = CliRunner().invoke(
        cli,
        ["auth", "login", "chatgpt", "--no-launch-login", "--no-open-browser"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [("chatgpt", False)]
    assert "Provider     : codex" in result.output


def test_auth_logout_deepseek_removes_keyring_secret(monkeypatch, tmp_path: Path) -> None:
    _patch_auth_env(monkeypatch, tmp_path)
    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        CliRunner().invoke(
            cli,
            [
                "auth",
                "login",
                "deepseek",
                "--api-key",
                "deepseek-secret",
                "--no-validate",
                "--no-open-browser",
            ],
        )

        result = CliRunner().invoke(cli, ["auth", "logout", "deepseek"])

        assert result.exit_code == 0, result.output
        assert resolve_llm_api_key("DEEPSEEK_API_KEY") == ""
    finally:
        keyring.set_keyring(previous_backend)
