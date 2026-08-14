from __future__ import annotations

from pathlib import Path

import keyring
from click.testing import CliRunner

from config.llm_credentials import resolve_env_credential
from surfaces.cli.app import cli
from surfaces.cli.llm_auth.service import AuthSetupResult
from tests.shared.keyring_backend import MemoryKeyring


def _patch_auth_env(monkeypatch, tmp_path: Path) -> Path:
    env_path = tmp_path / ".env"
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr("surfaces.cli.wizard.env_sync.PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr(
        "surfaces.cli.wizard.store.get_store_path", lambda: tmp_path / "opensre.json"
    )
    return env_path


def test_auth_login_deepseek_stores_secret_not_env(monkeypatch, tmp_path: Path) -> None:
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
        assert resolve_env_credential("DEEPSEEK_API_KEY") == "deepseek-secret"
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


def test_auth_verify_provider_reports_the_stored_source(monkeypatch, tmp_path: Path) -> None:
    """`opensre auth verify` names the tier that actually holds the secret."""
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
        assert "Source   : fallback" in result.output
        assert "local fallback credential store" in result.output
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

    monkeypatch.setattr(
        "surfaces.cli.commands.auth.configure_cli_subscription_provider", _fake_configure
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.auth.cli_subscription_install_error", lambda _profile: None
    )

    result = CliRunner().invoke(
        cli,
        ["auth", "login", "chatgpt", "--no-launch-login", "--no-open-browser"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [("chatgpt", False)]
    assert "Provider     : codex" in result.output


def test_auth_login_missing_cli_fails_before_any_prompt(monkeypatch, tmp_path: Path) -> None:
    """A missing vendor binary must fail immediately, not after the browser question."""
    # Arrange
    _patch_auth_env(monkeypatch, tmp_path)
    install_hint = "Codex CLI not found on PATH. Install: npm i -g @openai/codex"
    prompts: list[str] = []

    def _record_setup_page(profile, *, enabled):
        prompts.append(profile.name)

    def _must_not_configure(**_kwargs):
        raise AssertionError("configure must not run when the CLI is missing")

    monkeypatch.setattr(
        "surfaces.cli.commands.auth.cli_subscription_install_error",
        lambda _profile: install_hint,
    )
    monkeypatch.setattr("surfaces.cli.commands.auth._maybe_open_setup_page", _record_setup_page)
    monkeypatch.setattr(
        "surfaces.cli.commands.auth.configure_cli_subscription_provider", _must_not_configure
    )

    # Act
    result = CliRunner().invoke(cli, ["auth", "login", "chatgpt"])

    # Assert: guidance shown, no setup-page prompt, no configure attempt.
    assert result.exit_code != 0
    assert install_hint in result.output
    assert prompts == []


def test_provider_chooser_defaults_to_the_configured_provider(monkeypatch) -> None:
    """Bare `/auth login` must preselect the install's provider, not the first row."""
    import config.config as config_module
    from surfaces.cli.commands.auth import _configured_profile_name

    monkeypatch.setattr(config_module, "get_configured_llm_provider", lambda: "openai")
    assert _configured_profile_name() == "openai"

    # An OAuth install stores the backend provider; it maps to its profile name.
    monkeypatch.setattr(config_module, "get_configured_llm_provider", lambda: "codex")
    assert _configured_profile_name() == "chatgpt"


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
        assert resolve_env_credential("DEEPSEEK_API_KEY") == ""
    finally:
        keyring.set_keyring(previous_backend)
