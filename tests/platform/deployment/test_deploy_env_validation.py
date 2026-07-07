from __future__ import annotations

import pytest

from platform.deployment.ecr_deploy import prep


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

    with pytest.raises(prep.DeployEnvValidationError):
        prep.validate_deploy_env()

    output = capsys.readouterr().out
    assert "Deploy aborted: 3 required environment variable(s) missing" in output
    assert "MISSING: AWS credentials — not configured" in output
    assert "MISSING: TELEGRAM_BOT_TOKEN — API key not set" in output
    assert "MISSING: OPENAI_API_KEY — API key not set" in output
    assert "WARN: TELEGRAM_ALLOWED_USERS — not set" in output


def test_validate_deploy_env_allows_bedrock_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setattr(prep, "bootstrap_opensre_env", lambda **_kw: None)

    prep.validate_deploy_env()


def test_run_lifecycle_main_exits_cleanly_on_deploy_env_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _main() -> None:
        raise prep.DeployEnvValidationError

    with pytest.raises(SystemExit) as exc_info:
        prep.run_lifecycle_main(_main)

    assert exc_info.value.code == 1
