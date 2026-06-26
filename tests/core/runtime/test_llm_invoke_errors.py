from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import patch

from core.runtime.llm_invoke_errors import _looks_like_timeout, classify_llm_invoke_failure
from integrations.llm_cli.errors import CLITimeoutError


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
    from services.llm_retry import LLMCreditExhaustedError

    err = LLMCreditExhaustedError("OpenAI credit exhausted: insufficient_quota")
    assert classify_llm_invoke_failure(err) is None
