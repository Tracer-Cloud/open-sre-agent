"""Observe→answer loop: summarize read-only discovery output into an answer.

When the planner runs a read-only discovery command (e.g. ``/integrations``) to
answer a question like "is sentry installed?", the pipeline should follow up with
an assistant pass that summarizes the captured output, instead of leaving the user
with only a raw table.
"""

from __future__ import annotations

import io

from rich.console import Console

from context.session import ReplSession
from interactive_shell.agent_shell.agent import handle_message_with_agent
from interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from interactive_shell.utils.telemetry.recorder import LlmRunInfo

_OBSERVATION = "Integration status from `/integrations`:\n- sentry: missing (Not configured.)"


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def test_discovery_output_is_summarized_into_a_direct_answer() -> None:
    observed: list[str | None] = []

    def fake_execute(
        text: str,
        session: ReplSession,
        console: Console,
        **kwargs: object,
    ) -> ToolCallingTurnResult:
        session.last_command_observation = _OBSERVATION
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
        )

    def fake_answer(
        text: str,
        session: ReplSession,
        console: Console,
        **kwargs: object,
    ) -> LlmRunInfo:
        observed.append(kwargs.get("tool_observation"))  # type: ignore[arg-type]
        return LlmRunInfo(response_text="No — Sentry is not configured.")

    session = ReplSession()
    handle_message_with_agent(
        "is sentry installed?",
        session,
        _console(),
        recorder=None,
        execute_actions=fake_execute,
        answer_agent=fake_answer,
    )

    assert observed == [_OBSERVATION]
    assert session.last_assistant_intent == "cli_agent_summarized"


def test_no_observation_keeps_silent_handled_turn() -> None:
    """A command that produces no observation must not trigger a summary pass."""
    answer_calls: list[str] = []

    def fake_execute(
        text: str,
        session: ReplSession,
        console: Console,
        **kwargs: object,
    ) -> ToolCallingTurnResult:
        # No discovery observation recorded this turn.
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
        )

    def fake_answer(text: str, *args: object, **kwargs: object) -> None:
        answer_calls.append(text)
        return None

    session = ReplSession()
    handle_message_with_agent(
        "deploy the remote instance",
        session,
        _console(),
        recorder=None,
        execute_actions=fake_execute,
        answer_agent=fake_answer,
    )

    assert answer_calls == []
    assert session.last_assistant_intent == "cli_agent_handled"


def test_failed_discovery_is_not_summarized() -> None:
    """If the discovery command failed, skip the summary (output already shown)."""
    answer_calls: list[str] = []

    def fake_execute(
        text: str,
        session: ReplSession,
        console: Console,
        **kwargs: object,
    ) -> ToolCallingTurnResult:
        session.last_command_observation = _OBSERVATION
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=0,  # nothing succeeded
            has_unhandled_clause=False,
            handled=True,
        )

    def fake_answer(text: str, *args: object, **kwargs: object) -> None:
        answer_calls.append(text)
        return None

    session = ReplSession()
    handle_message_with_agent(
        "is sentry installed?",
        session,
        _console(),
        recorder=None,
        execute_actions=fake_execute,
        answer_agent=fake_answer,
    )

    assert answer_calls == []
    assert session.last_assistant_intent == "cli_agent_handled"


def test_observation_is_reset_each_turn() -> None:
    """A stale observation from a prior turn must not trigger a later summary."""
    answer_calls: list[object] = []

    def fake_execute(
        text: str,
        session: ReplSession,
        console: Console,
        **kwargs: object,
    ) -> ToolCallingTurnResult:
        # Does not set an observation this turn.
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
        )

    def fake_answer(text: str, *args: object, **kwargs: object) -> None:
        answer_calls.append(kwargs.get("tool_observation"))
        return None

    session = ReplSession()
    session.last_command_observation = "stale observation from a previous turn"
    handle_message_with_agent(
        "deploy the remote instance",
        session,
        _console(),
        recorder=None,
        execute_actions=fake_execute,
        answer_agent=fake_answer,
    )

    assert answer_calls == []
    assert session.last_command_observation is None
