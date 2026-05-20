from __future__ import annotations

import os
import stat

import pytest

from app.cli.wizard.config import PROVIDER_BY_VALUE
from app.cli.wizard.env_sync import (
    _is_sensitive_env_key,
    sync_env_values,
    sync_provider_env,
)
from app.llm_credentials import resolve_env_credential

_SKIP_AS_ROOT = not hasattr(os, "getuid") or os.getuid() == 0


@pytest.fixture(autouse=True)
def _isolate_os_environ() -> None:
    """Restore os.environ after each test that mutates process env."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


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
        "OPENAI_REASONING_MODEL=gpt-5.4\n"
        "OPENAI_MODEL=gpt-5.4\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n"
        "OPENAI_CLASSIFICATION_MODEL=gpt-5.4-mini\n"
        "CODEX_MODEL=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["codex"],
        model="gpt-5.4",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=codex\n" in content
    assert "CODEX_MODEL=gpt-5.4\n" in content
    assert "OPENAI_TOOLCALL_MODEL=" not in content
    assert "OPENAI_CLASSIFICATION_MODEL=" not in content
    assert "OPENAI_REASONING_MODEL=" not in content
    assert "OPENAI_TOOLCALL_MODEL" not in os.environ


def test_sync_provider_env_loads_preserved_keys_from_env_file(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4\n"
        "OPENAI_MODEL=gpt-5.4\n"
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
        model="gpt-5.4",
        env_path=env_path,
    )

    assert os.environ["LLM_PROVIDER"] == "openai"
    assert os.environ["OPENAI_REASONING_MODEL"] == "gpt-5.4"
    assert os.environ["OPENAI_TOOLCALL_MODEL"] == "gpt-5.4-mini"
    assert os.environ["OPENAI_CLASSIFICATION_MODEL"] == "gpt-5.4-mini"


def test_sync_provider_env_preserves_active_provider_toolcall_key(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4\n"
        "OPENAI_MODEL=gpt-5.4\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n" in content
    assert os.environ.get("OPENAI_TOOLCALL_MODEL") == "gpt-5.4-mini"


def test_sync_provider_env_updates_os_environ(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\nOPENAI_REASONING_MODEL=gpt-5.4\nOPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4")
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
        "OPENAI_REASONING_MODEL=gpt-5.4\n"
        "OPENAI_MODEL=gpt-5.4\n"
        "OPENAI_TOOLCALL_MODEL=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4")
    monkeypatch.delenv("OPENAI_TOOLCALL_MODEL", raising=False)

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4",
        env_path=env_path,
    )

    assert os.environ["OPENAI_REASONING_MODEL"] == "gpt-5.4"
    assert "OPENAI_TOOLCALL_MODEL" not in os.environ


def test_sync_provider_env_skips_empty_toolcall_model_override(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4\n"
        "OPENAI_MODEL=gpt-5.4\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4",
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
        "OPENAI_REASONING_MODEL=gpt-5.4\n"
        "OPENAI_MODEL=gpt-5.4\n"
        "OPENAI_TOOLCALL_MODEL=old-toolcall\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4")
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


def test_sync_env_values_routes_secrets_to_keyring(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GITLAB_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "tests.shared.keyring_backend.MemoryKeyring")

    env_path = tmp_path / ".env"
    env_path.write_text(
        "GITLAB_BASE_URL=https://gitlab.example.com\nGITLAB_ACCESS_TOKEN=legacy-plaintext\n",
        encoding="utf-8",
    )

    sync_env_values(
        {
            "GITLAB_BASE_URL": "https://gitlab.corp.com",
            "GITLAB_ACCESS_TOKEN": "gl-secret-token",
        },
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "GITLAB_BASE_URL=https://gitlab.corp.com\n" in content
    assert "GITLAB_ACCESS_TOKEN=" not in content
    assert resolve_env_credential("GITLAB_ACCESS_TOKEN") == "gl-secret-token"


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
