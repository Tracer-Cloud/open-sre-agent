"""Unit tests for LLM action planner prompt-overflow fallback."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.cli.interactive_shell.routing.handle_message_with_agent.errors import PlannerLLMError
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner.planner import (
    plan_actions_with_llm_result,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner.prompting import (
    _system_prompt,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner.system_prompt import (
    _SYSTEM_PROMPT_BASE,
)
from app.integrations.llm_cli.failure_explain import is_context_length_overflow


def test_system_prompt_does_not_reference_removed_slash_catalog() -> None:
    prompt = _system_prompt()
    assert prompt == _SYSTEM_PROMPT_BASE
    assert "slash catalog below" not in prompt.lower()
    assert "slash_invoke tool description" in prompt


def test_system_prompt_permits_read_only_discovery_for_factual_questions() -> None:
    """The planner must be free to run a read-only command to answer "is X installed?".

    Without this the planner deflects every factual question to assistant_handoff
    and never discovers the answer itself (see the integration-awareness change).
    """
    import re

    # Normalize whitespace so assertions don't depend on where the prompt
    # string happens to wrap across source lines.
    prompt = re.sub(r"\s+", " ", _system_prompt().lower())
    # The model is told it MAY emit a read-only discovery action and should not
    # tell the user to go run the command themselves.
    assert "read-only" in prompt
    assert "/integrations" in prompt
    assert "is sentry installed" in prompt
    # The planner is explicitly permitted to emit a read-only discovery action
    # for current-state questions instead of always handing off.
    assert "may emit that read-only discovery action" in prompt


def test_system_prompt_gates_diagnostic_dispatch_on_connected_integrations() -> None:
    """Investigation-intent diagnostic questions dispatch only when integrations are
    connected; explicit investigate instructions dispatch regardless.

    This encodes the Option A behavior change: the planner must read the
    CONNECTED INTEGRATIONS line and dispatch investigation_start for diagnostic
    questions only when at least one integration is connected.
    """
    import re

    prompt = re.sub(r"\s+", " ", _system_prompt().lower())
    # The gate line the planner reads.
    assert "connected integrations" in prompt
    # Diagnostic questions are an investigation request gated on integrations.
    assert "diagnostic question" in prompt
    assert "investigation_start" in prompt
    # Explicit investigate instructions are NOT gated.
    assert "regardless of" in prompt
    # The figure-out / query-sources phrasing is now a dispatch candidate, not a
    # hardcoded handoff.
    assert "figure out why x is crashing" in prompt


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("prompt is too long: 200001 tokens > 200000 maximum", True),
        (
            "Error code: 400 - This model's maximum context length is 128000 tokens",
            True,
        ),
        ("prompt too long — shorten the input or reduce accumulated context", True),
        ("Prompt too long: 65798 tokens exceeds max context window of 65536 tokens", True),
        ("The request took too long to complete", False),
        ("codex: quota or rate limit exceeded (exit 1)", False),
        ("authentication failed — verify your API key", False),
    ],
)
def test_is_context_length_overflow_matches_provider_messages(message: str, expected: bool) -> None:
    assert is_context_length_overflow(message) is expected


@pytest.mark.parametrize(
    "overflow_message",
    [
        "prompt is too long: 200001 tokens > 200000 maximum",
        "Error code: 400 - This model's maximum context length is 128000 tokens",
    ],
)
def test_plan_actions_with_llm_result_hands_off_on_prompt_overflow(overflow_message: str) -> None:
    # The planner is the sole tool selector. When the prompt is too long for the
    # planner LLM there is no regex fallback to guess an action, so the turn is
    # handed off to the conversational assistant rather than mis-routed.
    message = "show connected integrations"

    def _raise_overflow(*_args: object, **_kwargs: object) -> str:
        raise PlannerLLMError(overflow_message)

    with patch(
        "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration."
        "llm_action_planner.planner._call_llm",
        side_effect=_raise_overflow,
    ):
        result = plan_actions_with_llm_result(message)

    assert result is not None
    assert result.policy_trace[0] == "fallback_prompt_too_long"
    assert [(action.kind, action.content) for action in result.actions] == [
        ("assistant_handoff", message)
    ]
    assert result.has_unhandled_clause is False


def test_plan_actions_with_llm_result_re_raises_non_overflow_planner_errors() -> None:
    with (
        patch(
            "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration."
            "llm_action_planner.planner._call_llm",
            side_effect=PlannerLLMError("codex: quota or rate limit exceeded (exit 1)"),
        ),
        pytest.raises(PlannerLLMError, match="quota"),
    ):
        plan_actions_with_llm_result("check cpu usage")


def test_plan_actions_with_llm_result_re_raises_timeout_too_long_errors() -> None:
    with (
        patch(
            "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration."
            "llm_action_planner.planner._call_llm",
            side_effect=PlannerLLMError("The request took too long to complete"),
        ),
        pytest.raises(PlannerLLMError, match="too long"),
    ):
        plan_actions_with_llm_result("check cpu usage")


class _RaisingClient:
    """Stand-in classification client whose invoke always fails."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def bind_tools(self, _specs: object) -> _RaisingClient:
        return self

    def invoke(self, _prompt: object) -> object:
        raise self._error


def _patch_planner_llm(monkeypatch, error: Exception) -> None:
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration import (
        llm_action_planner,
    )

    monkeypatch.setattr(
        llm_action_planner.llm_client,
        "_tool_specs_for_provider",
        lambda _session: [],
    )
    monkeypatch.setattr(
        "app.services.llm_client.get_llm_for_classification",
        lambda: _RaisingClient(error),
    )


def test_call_llm_prefixes_fallback_provider_context(monkeypatch) -> None:
    # Configured openai but only anthropic has a key: the user-visible planner
    # error must say the call fell back to anthropic, instead of an opaque
    # "Anthropic credit balance too low" that contradicts their config.
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner.llm_client import (  # noqa: E501
        _call_llm,
    )
    from app.cli.interactive_shell.runtime.session import ReplSession

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "sk-present" if env_var == "ANTHROPIC_API_KEY" else "",
    )
    _patch_planner_llm(
        monkeypatch,
        RuntimeError("Anthropic request rejected (HTTP 400): Your credit balance is too low"),
    )

    with pytest.raises(PlannerLLMError) as excinfo:
        _call_llm("show me the logs", ReplSession())

    message = str(excinfo.value)
    assert message.startswith("[LLM provider: anthropic — fell back from configured 'openai'")
    assert "OPENAI_API_KEY not set" in message
    assert "credit balance is too low" in message


def test_call_llm_prefixes_active_provider_context_without_fallback(monkeypatch) -> None:
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner.llm_client import (  # noqa: E501
        _call_llm,
    )
    from app.cli.interactive_shell.runtime.session import ReplSession

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "sk-present" if env_var == "OPENAI_API_KEY" else "",
    )
    _patch_planner_llm(monkeypatch, RuntimeError("OpenAI billing quota exceeded."))

    with pytest.raises(PlannerLLMError) as excinfo:
        _call_llm("show me the logs", ReplSession())

    message = str(excinfo.value)
    assert message.startswith("[LLM provider: openai]")
    assert "billing quota exceeded" in message
