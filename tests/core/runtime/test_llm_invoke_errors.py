from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import patch

import pytest

from core.llm_invoke_errors import (
    LLM_PROVIDER_FAILURE_KINDS,
    ProviderFailureKind,
    _looks_like_timeout,
    classify_llm_invoke_failure,
    classify_llm_provider_failure,
    classify_provider_error_kind,
    is_cli_timeout_error,
    remediate_missing_llm_credentials,
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


def test_looks_like_timeout_recognizes_httpx_timeout_exception_by_class_name() -> None:
    """httpx.TimeoutException is not a TimeoutError and may not say 'timeout'."""

    class TimeoutException(Exception):
        """Stand-in for httpx.TimeoutException without importing httpx."""

    assert _looks_like_timeout(TimeoutException("deadline exceeded")) is True


def test_timeout_user_message_does_not_echo_exception_text() -> None:
    failure = classify_llm_invoke_failure(TimeoutError("deadline with token=sk-secret"))
    assert failure is not None
    assert failure.user_message == "Investigation stopped: the LLM request timed out."
    assert "sk-secret" not in failure.user_message
    assert "sk-secret" not in failure.tracker_message


def test_anthropic_model_not_found_user_message_is_generic() -> None:
    failure = classify_llm_invoke_failure(
        RuntimeError("anthropic: model 'claude-internal-alias' was not found")
    )
    assert failure is not None
    assert failure.user_message == (
        "Anthropic model was not found. Check your configured model name."
    )
    assert "claude-internal-alias" not in failure.user_message
    assert "claude-internal-alias" not in failure.tracker_message


def test_azure_deployment_not_found_user_message_is_generic() -> None:
    failure = classify_llm_invoke_failure(
        RuntimeError("Azure OpenAI deployment 'prod-secret-name' was not found (404).")
    )
    assert failure is not None
    assert failure.user_message == "The configured Azure OpenAI deployment was not found (404)."
    assert "prod-secret-name" not in failure.user_message
    assert "prod-secret-name" not in failure.tracker_message


def test_classify_returns_none_for_credit_exhausted_so_it_propagates() -> None:
    """LLMCreditExhaustedError must propagate instead of becoming a degraded result."""
    from core.llm.shared.llm_retry import LLMCreditExhaustedError

    err = LLMCreditExhaustedError("OpenAI credit exhausted: insufficient_quota")
    assert classify_llm_invoke_failure(err) is None


def test_auth_failure_user_message_does_not_echo_provider_exception_text() -> None:
    failure = classify_llm_invoke_failure(
        RuntimeError("AuthenticationError: invalid x-api-key sk-secret-fragment")
    )
    assert failure is not None
    assert failure.user_message == "Investigation stopped: LLM authentication failed."
    assert "sk-secret-fragment" not in failure.user_message
    assert "sk-secret-fragment" not in failure.tracker_message


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


_OPENAI_MISSING_KEY_MESSAGE = (
    "Missing credentials. Please pass an `api_key`, `workload_identity`, "
    "`admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` "
    "environment variable."
)


def test_remediate_missing_credentials_rewrites_sdk_message_with_login_command() -> None:
    # Arrange / Act: the exact OpenAI SDK text a key-less shell turn surfaces.
    text = remediate_missing_llm_credentials(_OPENAI_MISSING_KEY_MESSAGE, provider="openai")

    # Assert: in-shell commands first; do not echo the provider exception.

    assert text is not None
    assert "No API key is set for openai" in text
    assert "`/auth login openai`" in text
    assert "`/onboard`" in text
    assert "`opensre auth login openai`" in text
    assert "Missing credentials" not in text
    assert "OPENAI_API_KEY" not in text


def test_remediate_missing_credentials_without_provider_uses_placeholder() -> None:
    text = remediate_missing_llm_credentials(_OPENAI_MISSING_KEY_MESSAGE, provider=None)

    assert text is not None
    assert "No LLM API key is set" in text
    assert "/auth login <provider>" in text


@pytest.mark.parametrize(
    "message",
    [
        "Incorrect API key provided: sk-abc. You can find your key at platform.openai.com.",
        "Error code: 429 - rate limit exceeded",
        "The LLM request timed out after 300s.",
        # Rejected-key wrappers cite *_API_KEY env names; they must keep their
        # real authentication message, never the no-key guidance.
        "AuthenticationError: invalid x-api-key. Check your ANTHROPIC_API_KEY.",
        "401 Unauthorized: the key from OPENAI_API_KEY was rejected.",
        "API key expired. Renew the key stored in GEMINI_API_KEY.",
    ],
)
def test_remediate_missing_credentials_ignores_other_failures(message: str) -> None:
    assert remediate_missing_llm_credentials(message) is None


def test_remediate_missing_credentials_matches_wrapper_requires_env_message() -> None:
    text = remediate_missing_llm_credentials(
        "LLM provider 'anthropic' requires ANTHROPIC_API_KEY to be set.",
        provider="anthropic",
    )

    assert text is not None
    assert "`/auth login anthropic`" in text


def test_remediate_missing_credentials_matches_anthropic_absence_message() -> None:
    text = remediate_missing_llm_credentials(
        "Could not resolve authentication method. Expected either api_key or auth_token to be set.",
        provider="anthropic",
    )

    assert text is not None
    assert "`/auth login anthropic`" in text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (_OPENAI_MISSING_KEY_MESSAGE, ProviderFailureKind.MISSING_KEY),
        (
            "Could not resolve authentication method. Expected either api_key "
            "or auth_token to be set.",
            ProviderFailureKind.MISSING_KEY,
        ),
        (
            "AuthenticationError: invalid x-api-key. Check your ANTHROPIC_API_KEY.",
            ProviderFailureKind.REJECTED_KEY,
        ),
        ("Anthropic authentication failed.", ProviderFailureKind.REJECTED_KEY),
        ("Error code: 429 - too many requests", ProviderFailureKind.QUOTA),
        ("Anthropic model 'claude-x' was not found.", ProviderFailureKind.NOT_CONFIGURED),
        ("something unexpected exploded", ProviderFailureKind.PROVIDER_ERROR),
    ],
)
def test_classify_llm_provider_failure_rule_order(
    message: str, expected: ProviderFailureKind
) -> None:
    assert classify_llm_provider_failure(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        _OPENAI_MISSING_KEY_MESSAGE,
        "Could not resolve authentication method. Expected either api_key or auth_token to be set.",
        "LLM provider 'anthropic' requires ANTHROPIC_API_KEY to be set.",
        "AuthenticationError: invalid x-api-key. Check your ANTHROPIC_API_KEY.",
        "401 Unauthorized: the key from OPENAI_API_KEY was rejected.",
        "Error code: 429 - rate limit exceeded",
        "something unexpected exploded",
    ],
)
def test_remediation_and_analytics_always_agree(message: str) -> None:
    """One classification, two consumers: guidance fires iff analytics says not-configured-ish.

    Regression: the split classifiers disagreed on Anthropic's absence message
    (analytics said ``auth`` while the user saw missing-key guidance).
    """
    remediated = remediate_missing_llm_credentials(message) is not None
    kind = classify_llm_provider_failure(message)
    analytics = classify_provider_error_kind(message)

    assert remediated == (kind is ProviderFailureKind.MISSING_KEY)
    if remediated:
        assert analytics == "not_configured"
        assert analytics != "auth"
