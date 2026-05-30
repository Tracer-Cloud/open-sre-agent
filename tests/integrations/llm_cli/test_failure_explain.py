from __future__ import annotations

from app.integrations.llm_cli.failure_explain import (
    classify_cli_failure_hint,
    explain_cli_failure,
)


def test_classify_quota_hint() -> None:
    hint = classify_cli_failure_hint("", "429 Too Many Requests: quota exceeded", 1)
    assert hint is not None
    assert "quota" in hint


def test_classify_silent_exit_hint() -> None:
    hint = classify_cli_failure_hint("", "OpenAI Codex v0.134.0", 1)
    assert hint is not None
    assert "quota exhausted" in hint


def test_explain_cli_failure_with_extra_messages() -> None:
    msg = explain_cli_failure(
        exit_label="kimi",
        stdout="",
        stderr="LLM not set",
        returncode=1,
        extra_messages=("Not logged in or model unavailable. Run: kimi login",),
    )
    assert "kimi exited with code 1" in msg
    assert "kimi login" in msg
    assert "quota" not in msg.lower()


def test_explain_cli_failure_generic_quota() -> None:
    msg = explain_cli_failure(
        exit_label="codex exec",
        stdout="",
        stderr="rate limit exceeded",
        returncode=1,
    )
    assert "codex exec exited with code 1" in msg
    assert "quota or rate limit" in msg
