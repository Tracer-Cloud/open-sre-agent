from __future__ import annotations

from integrations.llm_cli.failure_explain import (
    classify_cli_failure_category_hint,
    classify_cli_failure_hint,
    explain_cli_failure,
    is_context_length_overflow,
)


def test_is_context_length_overflow_distinguishes_timeouts() -> None:
    assert is_context_length_overflow("prompt is too long: 200001 tokens > 200000 maximum")
    assert not is_context_length_overflow("The request took too long to complete")


def test_classify_context_hint_ignores_timeout_too_long() -> None:
    hint = classify_cli_failure_category_hint("", "The request took too long to complete", 1)
    assert hint is None


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


def test_explain_cli_failure_prefers_stdout_over_silent_hint() -> None:
    msg = explain_cli_failure(
        exit_label="claude -p",
        stdout="some output",
        stderr="",
        returncode=2,
    )
    assert "some output" in msg
    assert "quota exhausted" not in msg


def test_explain_cli_failure_empty_extra_messages_falls_through() -> None:
    msg = explain_cli_failure(
        exit_label="codex exec",
        stdout="",
        stderr="rate limit exceeded",
        returncode=1,
        extra_messages=("",),
    )
    assert "quota or rate limit" in msg


def test_explain_cli_failure_always_include_output_snippet_without_extra() -> None:
    msg = explain_cli_failure(
        exit_label="copilot -p",
        stdout="model error details",
        stderr="",
        returncode=1,
        always_include_output_snippet=True,
    )
    assert "copilot -p exited with code 1" in msg
    assert "model error details" in msg
    assert "quota or rate limit" not in msg
