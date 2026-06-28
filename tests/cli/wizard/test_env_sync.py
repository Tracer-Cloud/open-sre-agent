from __future__ import annotations

import os
import stat

import keyring
import pytest

from cli.wizard.config import PROVIDER_BY_VALUE
from cli.wizard.env_sync import (
    _is_sensitive_env_key,
    sync_env_secret,
    sync_env_values,
    sync_provider_env,
    sync_reasoning_model_env,
)
from cli.wizard.store import load_local_config
from config.llm_credentials import resolve_env_credential
from tests.shared.keyring_backend import MemoryKeyring

_SKIP_AS_ROOT = not hasattr(os, "getuid") or os.getuid() == 0


@pytest.fixture(autouse=True)
def _redirect_wizard_store(tmp_path, monkeypatch) -> None:
    """Keep sync_provider_env store updates off the developer's ~/.opensre."""
    monkeypatch.setattr(
        "cli.wizard.store.get_store_path",
        lambda: tmp_path / "opensre.json",
    )


@pytest.mark.parametrize(
    "key",
    [
        # Suffix-style sensitive keys (existing behavior).
        "GITLAB_ACCESS_TOKEN",
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "DB_PASSWORD",
        # Bare sensitive keys (previously slipped past the suffix-only filter
        # and reached plain-text .env, which CodeQL flagged as alert #1019).
        "PASSWORD",
        "TOKEN",
        "SECRET",
        "KEY",
        "APIKEY",
        "CREDENTIAL",
        # Substring-based sensitives.
        "DATABASE_CONNECTION_STRING",
    ],
)
def test_is_sensitive_env_key_marks_secrets(key: str) -> None:
    assert _is_sensitive_env_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        # Non-secret configuration env vars must stay writable to .env.
        "LLM_PROVIDER",
        "OPENAI_REASONING_MODEL",
        "OPENAI_MODEL",
        "GITLAB_BASE_URL",
        "ENV",
        # Terminal token is not in the sensitive set even though "TOKEN"
        # appears as a substring — the limit count is not itself a secret.
        "OPENAI_TOKEN_LIMIT",
        # Explicit exception: a public discord key is not sensitive.
        "DISCORD_PUBLIC_KEY",
    ],
)
def test_is_sensitive_env_key_leaves_non_secrets(key: str) -> None:
    assert _is_sensitive_env_key(key) is False


def test_sync_provider_env_updates_provider_specific_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENV=development\n"
        "LLM_PROVIDER=anthropic\n"
        "ANTHROPIC_API_KEY=legacy-anthropic\n"
        "OPENAI_API_KEY=old-key\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "ENV=development\n" in content
    assert content.count("LLM_PROVIDER=") == 1
    assert "LLM_PROVIDER=openai\n" in content
    assert "OPENAI_API_KEY=" not in content
    assert "ANTHROPIC_API_KEY=" not in content
    assert "OPENAI_REASONING_MODEL=gpt-5-mini\n" in content
    assert "OPENAI_MODEL=gpt-5-mini\n" in content


def test_sync_provider_env_strips_integration_fallback_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_BOT_TOKEN=manual-fallback-token\nLLM_PROVIDER=anthropic\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=" not in content
    assert "LLM_PROVIDER=openai\n" in content


def test_sync_provider_env_updates_wizard_store(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5-mini",
        env_path=env_path,
    )

    stored = load_local_config(store_path)
    local = stored["targets"]["local"]
    assert local["provider"] == "openai"
    assert local["model"] == "gpt-5-mini"
    assert local["api_key_env"] == "OPENAI_API_KEY"
    assert local["model_env"] == "OPENAI_REASONING_MODEL"


def test_sync_reasoning_model_env_updates_wizard_store(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    sync_reasoning_model_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    stored = load_local_config(store_path)
    local = stored["targets"]["local"]
    assert local["provider"] == "openai"
    assert local["model"] == "gpt-5.4-mini"
    assert env_path.read_text(encoding="utf-8") == (
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\nOPENAI_MODEL=gpt-5.4-mini\n"
    )


def test_sync_provider_env_appends_to_file_without_final_newline(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENV=development\n"
        "LLM_PROVIDER=anthropic\n"
        "ANTHROPIC_API_KEY=legacy-anthropic\n"
        "TEST_ENV=no-new-line",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert content.endswith("OPENAI_MODEL=gpt-5-mini\n")
    assert "LLM_PROVIDER=openai\n" in content
    assert "ANTHROPIC_API_KEY=" not in content


def test_sync_provider_env_codex_writes_codex_model(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    sync_provider_env(
        provider=PROVIDER_BY_VALUE["codex"],
        model="",
        env_path=env_path,
    )
    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=codex\n" in content
    assert "CODEX_MODEL=\n" in content


def test_sync_provider_env_openai_oauth_writes_auth_method_and_codex_model(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_AUTH_METHOD", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=anthropic\nANTHROPIC_REASONING_MODEL=claude-opus-4-7\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="",
        model_provider=PROVIDER_BY_VALUE["codex"],
        auth_method="oauth",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=openai\n" in content
    assert "LLM_AUTH_METHOD=oauth\n" in content
    assert "CODEX_MODEL=\n" in content
    assert "ANTHROPIC_REASONING_MODEL=" not in content
    assert os.environ["LLM_PROVIDER"] == "openai"
    assert os.environ["LLM_AUTH_METHOD"] == "oauth"
    assert os.environ["CODEX_MODEL"] == ""


def test_sync_provider_env_gemini_cli_writes_model(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_CLI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    sync_provider_env(
        provider=PROVIDER_BY_VALUE["gemini-cli"],
        model="",
        env_path=env_path,
    )
    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=gemini-cli\n" in content
    assert "GEMINI_CLI_MODEL=\n" in content


def test_sync_provider_env_removes_stale_toolcall_and_classification_keys(
    tmp_path, monkeypatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n"
        "OPENAI_CLASSIFICATION_MODEL=gpt-5.4-mini\n"
        "CODEX_MODEL=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["codex"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=codex\n" in content
    assert "CODEX_MODEL=gpt-5.4-mini\n" in content
    assert "OPENAI_TOOLCALL_MODEL=" not in content
    assert "OPENAI_CLASSIFICATION_MODEL=" not in content
    assert "OPENAI_REASONING_MODEL=" not in content
    assert "OPENAI_TOOLCALL_MODEL" not in os.environ


def test_sync_provider_env_loads_preserved_keys_from_env_file(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n"
        "OPENAI_CLASSIFICATION_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_TOOLCALL_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_CLASSIFICATION_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "stale")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    assert os.environ["LLM_PROVIDER"] == "openai"
    assert os.environ["OPENAI_REASONING_MODEL"] == "gpt-5.4-mini"
    assert os.environ["OPENAI_TOOLCALL_MODEL"] == "gpt-5.4-mini"
    assert os.environ["OPENAI_CLASSIFICATION_MODEL"] == "gpt-5.4-mini"


def test_sync_provider_env_preserves_active_provider_toolcall_key(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n" in content
    assert os.environ.get("OPENAI_TOOLCALL_MODEL") == "gpt-5.4-mini"


def test_sync_provider_env_updates_os_environ(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\nOPENAI_REASONING_MODEL=gpt-5.4-mini\nOPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["codex"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    assert os.environ["LLM_PROVIDER"] == "codex"
    assert os.environ["CODEX_MODEL"] == "gpt-5.4-mini"
    assert "OPENAI_TOOLCALL_MODEL" not in os.environ
    assert "OPENAI_REASONING_MODEL" not in os.environ


def test_sync_provider_env_skips_empty_preserved_values_in_os_environ(
    tmp_path, monkeypatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.delenv("OPENAI_TOOLCALL_MODEL", raising=False)

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    assert os.environ["OPENAI_REASONING_MODEL"] == "gpt-5.4-mini"
    assert "OPENAI_TOOLCALL_MODEL" not in os.environ


def test_sync_provider_env_skips_empty_toolcall_model_override(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        toolcall_model="",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n" in content
    assert os.environ["OPENAI_TOOLCALL_MODEL"] == "gpt-5.4-mini"


def test_sync_provider_env_writes_toolcall_model_atomically(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=old-toolcall\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "old-toolcall")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        toolcall_model="gpt-5.4-nano",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "OPENAI_REASONING_MODEL=gpt-5.4-mini\n" in content
    assert "OPENAI_TOOLCALL_MODEL=gpt-5.4-nano\n" in content
    assert os.environ["OPENAI_REASONING_MODEL"] == "gpt-5.4-mini"
    assert os.environ["OPENAI_TOOLCALL_MODEL"] == "gpt-5.4-nano"


@pytest.mark.skipif(_SKIP_AS_ROOT, reason="root bypasses file permission checks")
def test_sync_provider_env_permission_error(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    env_path.chmod(stat.S_IRUSR)  # read-only
    try:
        with pytest.raises(PermissionError, match="permission denied"):
            sync_provider_env(
                provider=PROVIDER_BY_VALUE["openai"],
                model="gpt-4o",
                env_path=env_path,
            )
    finally:
        env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_sync_env_values_rejects_sensitive_keys(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sync_env_secret"):
        sync_env_values({"GITLAB_ACCESS_TOKEN": "secret"}, env_path=env_path)


def test_sync_env_values_routes_secrets_to_keyring(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GITLAB_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        env_path = tmp_path / ".env"
        env_path.write_text(
            "GITLAB_BASE_URL=https://gitlab.example.com\nGITLAB_ACCESS_TOKEN=legacy-plaintext\n",
            encoding="utf-8",
        )

        sync_env_secret("GITLAB_ACCESS_TOKEN", "gl-secret-token")
        sync_env_values(
            {"GITLAB_BASE_URL": "https://gitlab.corp.com"},
            env_path=env_path,
        )

        content = env_path.read_text(encoding="utf-8")
        assert "GITLAB_BASE_URL=https://gitlab.corp.com\n" in content
        assert "GITLAB_ACCESS_TOKEN=" not in content
        assert resolve_env_credential("GITLAB_ACCESS_TOKEN") == "gl-secret-token"
    finally:
        keyring.set_keyring(previous_backend)


@pytest.mark.skipif(_SKIP_AS_ROOT, reason="root bypasses file permission checks")
def test_sync_env_values_permission_error(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\n", encoding="utf-8")
    env_path.chmod(stat.S_IRUSR)
    try:
        with pytest.raises(PermissionError, match="permission denied"):
            sync_env_values({"FOO": "baz"}, env_path=env_path)
    finally:
        env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_strip_keyring_backed_secret_lines_removes_all_sensitive_lines() -> None:
    from cli.wizard.env_sync import _strip_keyring_backed_secret_lines

    lines = [
        "TELEGRAM_BOT_TOKEN=fallback\n",
        "DD_API_KEY=in-keyring\n",
        "DD_SITE=old\n",
    ]

    kept = _strip_keyring_backed_secret_lines(lines)

    assert kept == ["DD_SITE=old\n"]


def test_sync_env_values_strips_telegram_bot_token_fallback(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_BOT_TOKEN=manual-fallback-token\nDD_SITE=old\n",
        encoding="utf-8",
    )

    sync_env_values({"DD_SITE": "datadoghq.eu"}, env_path=env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=" not in content
    assert "DD_SITE=datadoghq.eu" in content


def test_sync_env_values_strips_multiple_fallback_secrets(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DD_API_KEY=datadog-api\nDD_APP_KEY=datadog-app\n",
        encoding="utf-8",
    )

    sync_env_values({"DD_SITE": "datadoghq.com"}, env_path=env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "DD_API_KEY=" not in content
    assert "DD_APP_KEY=" not in content
    assert "DD_SITE=datadoghq.com" in content


def test_sync_env_values_empty_update_strips_fallback_secrets(tmp_path) -> None:
    """Wizard paths call ``sync_env_values({})`` after integration setup."""
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_BOT_TOKEN=manual-fallback-token\n", encoding="utf-8")

    sync_env_values({}, env_path=env_path)

    assert env_path.read_text(encoding="utf-8") == ""
