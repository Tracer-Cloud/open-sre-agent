"""Tests for core LLM failure string classification."""

from __future__ import annotations

from core.llm.failure_classification import (
    classify_cli_failure_category_hint,
    classify_cli_failure_hint,
    is_context_length_overflow,
)


def test_is_context_length_overflow_distinguishes_timeouts() -> None:
    assert is_context_length_overflow("prompt is too long: 200001 tokens > 200000 maximum")
    assert not is_context_length_overflow("The request took too long to complete")


def test_classify_cli_failure_category_hint_quota() -> None:
    hint = classify_cli_failure_category_hint("", "rate limit exceeded", 1)
    assert hint is not None
    assert "rate limit" in hint


def test_classify_cli_failure_hint_silent_exit() -> None:
    hint = classify_cli_failure_hint("", "", 1)
    assert hint is not None
    assert "no error detail" in hint
