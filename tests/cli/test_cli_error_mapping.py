from __future__ import annotations

import pytest

from app.cli.support.cli_error_mapping import reraise_cli_runtime_error
from app.cli.support.errors import OpenSREError


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


def test_non_anthropic_model_not_found_does_not_match() -> None:
    """A 'model not found' error from a non-Anthropic provider must not trigger the Anthropic branch."""
    exc = RuntimeError("OpenAI model 'gpt-99' was not found. Check your configuration.")
    with pytest.raises(RuntimeError):
        reraise_cli_runtime_error(exc)


def test_cli_not_found_still_maps_correctly() -> None:
    """Existing CLI-not-found branch must still work after the new branch was added."""
    exc = RuntimeError("CLI not found on path: codex")
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(exc)

    assert "CLI tool is not installed" in str(exc_info.value)
