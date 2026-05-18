from __future__ import annotations

import os
import stat

import pytest

from app.cli.wizard.config import PROVIDER_BY_VALUE
from app.cli.wizard.env_sync import sync_env_values, sync_provider_env

_SKIP_AS_ROOT = not hasattr(os, "getuid") or os.getuid() == 0


def test_sync_provider_env_updates_provider_specific_keys(tmp_path) -> None:
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


def test_sync_provider_env_appends_to_file_without_final_newline(tmp_path) -> None:
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


def test_sync_provider_env_codex_writes_codex_model(tmp_path) -> None:
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


def test_sync_provider_env_gemini_cli_writes_model(tmp_path) -> None:
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
