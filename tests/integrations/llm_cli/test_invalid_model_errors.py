"""Tests for structured invalid-model errors from LLM CLI adapters."""

from __future__ import annotations

import pytest

from app.integrations.llm_cli.claude_code import ClaudeCodeAdapter
from app.integrations.llm_cli.codex import CodexAdapter
from app.integrations.llm_cli.errors import CLIInvalidModelError


def test_codex_explain_failure_raises_invalid_model_error() -> None:
    adapter = CodexAdapter()

    with pytest.raises(CLIInvalidModelError) as exc_info:
        adapter.explain_failure(
            stdout="",
            stderr="error: invalid model 'not-a-real-model'",
            returncode=1,
        )

    assert exc_info.value.provider == "codex"
    assert "invalid model" in exc_info.value.detail


def test_claude_code_explain_failure_raises_invalid_model_error() -> None:
    adapter = ClaudeCodeAdapter()

    with pytest.raises(CLIInvalidModelError) as exc_info:
        adapter.explain_failure(
            stdout="",
            stderr="Unknown model: claude-made-up-9",
            returncode=1,
        )

    assert exc_info.value.provider == "claude-code"
    assert "Unknown model" in exc_info.value.detail


def test_codex_explain_failure_keeps_generic_error_message() -> None:
    adapter = CodexAdapter()

    message = adapter.explain_failure(
        stdout="",
        stderr="permission denied",
        returncode=1,
    )

    assert message == "codex exec exited with code 1. permission denied"
