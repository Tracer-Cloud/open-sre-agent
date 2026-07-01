from __future__ import annotations

import pytest

from platform.deployment import prep


def test_validate_deploy_env_passes_with_required_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "123")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(prep, "bootstrap_opensre_env", lambda **_kw: None)

    prep.validate_deploy_env()


def test_validate_deploy_env_lists_missing_required_vars(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_ROLE_ARN", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(prep, "_aws_credentials_available", lambda: False)
    monkeypatch.setattr(prep, "bootstrap_opensre_env", lambda **_kw: None)
    monkeypatch.setattr(prep, "get_configured_llm_provider", lambda: "openai")
    monkeypatch.setattr(prep, "get_project_env_path", lambda: "/tmp/.env")

    with pytest.raises(RuntimeError, match="Deploy aborted"):
        prep.validate_deploy_env()

    output = capsys.readouterr().out
    assert "MISSING: AWS account access for EC2 provisioning" in output
    assert "MISSING: Telegram gateway bot configuration" in output
    assert "MISSING: LLM provider configuration for the selected provider" in output
    assert "WARN: Telegram allowed-users configuration (recommended)" in output


def test_validate_deploy_env_allows_bedrock_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setattr(prep, "bootstrap_opensre_env", lambda **_kw: None)

    prep.validate_deploy_env()
