"""Unit tests for LLM action planner prompt-overflow fallback."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.cli.interactive_shell.routing.handle_message_with_agent.errors import PlannerLLMError
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner.constants import (
    _SYSTEM_PROMPT_BASE,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner.planner import (
    plan_actions_with_llm_result,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner.prompting import (
    _system_prompt,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper import (
    DeterministicMappingResult,
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


def test_plan_actions_with_llm_result_falls_back_on_anthropic_prompt_overflow() -> None:
    message = "show connected integrations"

    def _raise_overflow(*_args: object, **_kwargs: object) -> str:
        raise PlannerLLMError("prompt is too long: 200001 tokens > 200000 maximum")

    with patch(
        "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration."
        "llm_action_planner.planner._call_llm",
        side_effect=_raise_overflow,
    ):
        result = plan_actions_with_llm_result(message)

    assert result is not None
    assert result.policy_trace[0] == "fallback_prompt_too_long"
    assert [(action.kind, action.content) for action in result.actions] == [
        ("slash", "/integrations list")
    ]


def test_plan_actions_with_llm_result_falls_back_on_openai_context_overflow() -> None:
    message = "show connected integrations"

    def _raise_overflow(*_args: object, **_kwargs: object) -> str:
        raise PlannerLLMError(
            "Error code: 400 - This model's maximum context length is 128000 tokens"
        )

    with patch(
        "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration."
        "llm_action_planner.planner._call_llm",
        side_effect=_raise_overflow,
    ):
        result = plan_actions_with_llm_result(message)

    assert result is not None
    assert result.policy_trace[0] == "fallback_prompt_too_long"
    assert [(action.kind, action.content) for action in result.actions] == [
        ("slash", "/integrations list")
    ]


def test_plan_actions_with_llm_result_keeps_mapped_slash_for_informational_question() -> None:
    message = "Which integrations are connected?"

    def _raise_overflow(*_args: object, **_kwargs: object) -> str:
        raise PlannerLLMError("prompt is too long: 200001 tokens > 200000 maximum")

    with patch(
        "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration."
        "llm_action_planner.planner._call_llm",
        side_effect=_raise_overflow,
    ):
        result = plan_actions_with_llm_result(message)

    assert result is not None
    assert result.policy_trace[0] == "fallback_prompt_too_long"
    assert [(action.kind, action.content) for action in result.actions] == [
        ("slash", "/integrations list")
    ]


def test_plan_actions_with_llm_result_handoffs_when_mapper_returns_empty() -> None:
    message = "summarize recent deploy impact on checkout latency"
    empty_result = DeterministicMappingResult(
        actions=(),
        has_unhandled_clause=False,
        applied_policies=(),
    )

    def _raise_overflow(*_args: object, **_kwargs: object) -> str:
        raise PlannerLLMError("prompt is too long: 200001 tokens > 200000 maximum")

    with (
        patch(
            "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration."
            "llm_action_planner.planner._call_llm",
            side_effect=_raise_overflow,
        ),
        patch(
            "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration."
            "slash_commands.deterministic_action_mapper.map_actions_result",
            return_value=empty_result,
        ),
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
