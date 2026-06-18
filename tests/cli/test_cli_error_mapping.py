from __future__ import annotations

import pytest

from app.cli.support.cli_error_mapping import reraise_cli_runtime_error
from app.cli.support.errors import OpenSREError
from app.integrations.llm_cli.errors import CLITimeoutError


def test_anthropic_model_not_found_raises_opensre_error() -> None:
    """RuntimeError from an invalid Anthropic model name maps to a user-friendly OpenSREError."""
    exc = RuntimeError(
        "Anthropic model 'not-a-real-model-xyz' was not found. "
        "Check your configured model name and try again."
    )
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(exc)

    err = exc_info.value
    assert "not-a-real-model-xyz" in str(err)
    assert err.suggestion is not None
    assert "ANTHROPIC_REASONING_MODEL" in err.suggestion
    assert "ANTHROPIC_TOOLCALL_MODEL" in err.suggestion


def test_anthropic_model_not_found_suggestion_guides_env_vars() -> None:
    """The suggestion must point at the two env vars users are most likely to misconfigure."""
    exc = RuntimeError(
        "Anthropic model 'bad-model' was not found. Check your configured model name and try again."
    )
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(exc)

    assert exc_info.value.suggestion is not None
    assert "ANTHROPIC_REASONING_MODEL" in exc_info.value.suggestion
    assert "ANTHROPIC_TOOLCALL_MODEL" in exc_info.value.suggestion


def test_non_anthropic_model_not_found_maps_to_generic_opensre_error() -> None:
    """A 'model not found' error from a non-Anthropic provider uses the generic 404 guidance."""
    exc = RuntimeError("OpenAI model 'gpt-99' was not found. Check your configuration.")
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(exc)

    assert "not found" in str(exc_info.value).lower()
    assert exc_info.value.suggestion is not None
    assert "ANTHROPIC_REASONING_MODEL" not in exc_info.value.suggestion


def test_cli_not_found_still_maps_correctly() -> None:
    """Existing CLI-not-found branch must still work after the new branch was added."""
    exc = RuntimeError("CLI not found on path: codex")
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(exc)

    assert "CLI tool is not installed" in str(exc_info.value)


def test_runtime_prompt_too_long_with_unclear_auth_maps_to_opensre_error() -> None:
    exc = RuntimeError(
        "cursor agent exited with code 1. prompt too long — shorten the input or reduce "
        "accumulated context (/context to inspect)\n\nAuth status could not be verified "
        "before invocation. Run: agent login."
    )
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(exc)

    err = exc_info.value
    assert str(err) == "LLM invocation failed."
    assert err.suggestion is not None
    assert "prompt too long" in err.suggestion
    assert "Auth status could not be verified before invocation" in err.suggestion


def test_cli_timeout_maps_to_opensre_error() -> None:
    exc = CLITimeoutError("gemini-cli CLI timed out after 300s.")
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(exc)

    err = exc_info.value
    assert "timed out" in str(err).lower()
    assert err.suggestion is not None
    assert "GEMINI_CLI_TIMEOUT_SECONDS" in err.suggestion


def test_bedrock_model_not_available_maps_to_opensre_error() -> None:
    exc = RuntimeError(
        "Bedrock model 'us.anthropic.claude-sonnet-4-6' is not available for your account. "
        "Check Bedrock model access in the configured AWS region, AWS Marketplace "
        "subscription/payment setup, and IAM permissions including "
        "aws-marketplace:ViewSubscriptions and aws-marketplace:Subscribe."
    )

    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(exc)

    err = exc_info.value
    assert "Bedrock model" in str(err)
    assert err.suggestion is not None
    assert "AWS Marketplace" in err.suggestion
    assert "aws-marketplace:ViewSubscriptions" in err.suggestion
    assert "aws-marketplace:Subscribe" in err.suggestion
