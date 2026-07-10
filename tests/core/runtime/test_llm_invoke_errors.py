from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import patch

import pytest

from core.llm_invoke_errors import (
    LLM_PROVIDER_FAILURE_KINDS,
    _looks_like_timeout,
    classify_llm_invoke_failure,
    classify_provider_error_kind,
    is_cli_timeout_error,
)
from integrations.llm_cli.errors import CLITimeoutError


def test_is_cli_timeout_error_recognizes_cli_timeout_without_isinstance() -> None:
    assert is_cli_timeout_error(CLITimeoutError("gemini-cli CLI timed out after 300s."))
    assert not is_cli_timeout_error(RuntimeError("request timed out"))


def test_timeout_remediation_does_not_repeat_user_message() -> None:
    failure = classify_llm_invoke_failure(CLITimeoutError("gemini-cli CLI timed out after 300s."))
    assert failure is not None
    assert "timed out after 300s" in failure.user_message
    assert failure.remediation_steps
    assert not any("timed out after 300s" in step for step in failure.remediation_steps)


def test_looks_like_timeout_without_anthropic_sdk() -> None:
    """Classifier must not import anthropic at module level or break when SDK is absent."""
    fake_anthropic = ModuleType("anthropic")
    with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
        assert _looks_like_timeout(TimeoutError("deadline")) is True
        assert _looks_like_timeout(RuntimeError("request timed out")) is True


def test_classify_returns_none_for_credit_exhausted_so_it_propagates() -> None:
    """LLMCreditExhaustedError must propagate instead of becoming a degraded result."""
    from core.llm.shared.llm_retry import LLMCreditExhaustedError

    err = LLMCreditExhaustedError("OpenAI credit exhausted: insufficient_quota")
    assert classify_llm_invoke_failure(err) is None


def test_cli_auth_required_uses_unknown_provider_when_attr_missing() -> None:
    CLIAuthenticationRequired = type(
        "CLIAuthenticationRequired",
        (Exception,),
        {},
    )
    CLIAuthenticationRequired.__module__ = "integrations.llm_cli.errors"

    failure = classify_llm_invoke_failure(CLIAuthenticationRequired())
    assert failure is not None
    assert "unknown CLI is not authenticated" in failure.user_message
    assert failure.remediation_steps == [
        "Run `opensre doctor` to verify CLI installation and auth.",
    ]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Bedrock model 'us.anthropic.claude-sonnet-4-6' is not available for your "
            "account. Check Bedrock model access in the configured AWS region, AWS "
            "Marketplace subscription/payment setup, and IAM permissions.",
            "not_configured",
        ),
        ("LLM provider 'anthropic' requires ANTHROPIC_API_KEY to be set.", "not_configured"),
        (
            "Gemini model 'gemini-pro' is not configured or billing is not enabled: x",
            "not_configured",
        ),
        ("Anthropic model 'claude-x' was not found.", "not_configured"),
        ("LLM client unavailable: No module named 'anthropic'", "not_configured"),
        ("OpenAI rate limit exceeded after 6 attempts.", "quota"),
        ("Error code: 429 - too many requests", "quota"),
        ("Your credit balance is too low to access the Anthropic API.", "quota"),
        ("Anthropic authentication failed.", "auth"),
        ("openai request forbidden: 403", "auth"),
        ("invalid api key provided", "auth"),
        ("Incorrect api_key value provided", "auth"),
        ("Your api_key is invalid", "auth"),
        ("anthropic CLI timed out after 300s.", "provider_error"),
        ("something unexpected exploded", "provider_error"),
    ],
)
def test_classify_provider_error_kind(message: str, expected: str) -> None:
    assert classify_provider_error_kind(message) == expected


def test_llm_provider_failure_kinds_exclude_terminal_task_kinds() -> None:
    """Background-task/investigation error kinds must never count as LLM provider failures."""
    for terminal_kind in ("timeout", "cli_exit_nonzero", "spawn_failed", "unknown", "config"):
        assert terminal_kind not in LLM_PROVIDER_FAILURE_KINDS


def test_cli_auth_required_filters_none_remediation_fields() -> None:
    CLIAuthenticationRequired = type(
        "CLIAuthenticationRequired",
        (Exception,),
        {"provider": "codex", "auth_hint": None, "detail": ""},
    )
    CLIAuthenticationRequired.__module__ = "integrations.llm_cli.errors"

    failure = classify_llm_invoke_failure(CLIAuthenticationRequired())
    assert failure is not None
    assert "codex CLI is not authenticated" in failure.user_message
    assert failure.remediation_steps == [
        "Run `opensre doctor` to verify CLI installation and auth.",
    ]
