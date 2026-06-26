"""Observe→answer loop: summarize read-only discovery output into an answer.

When the planner runs a read-only discovery command (e.g. ``/integrations``) to
answer a question like "is sentry installed?", the pipeline should follow up with
an assistant pass that summarizes the captured output, instead of leaving the user
with only a raw table.
"""

from __future__ import annotations

import io

from rich.console import Console

from cli.interactive_shell.prompt_logging.recorder import LlmRunInfo
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from cli.interactive_shell.routing.handle_message_with_agent.pipeline import (
    handle_message_with_agent,
)
from cli.interactive_shell.runtime.session import ReplSession

_OBSERVATION = "Integration status from `/integrations`:\n- sentry: missing (Not configured.)"


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def _never_dispatch(*_args: object, **_kwargs: object) -> bool:  # pragma: no cover - guard
    raise AssertionError("deterministic dispatch should not run for a free-text question")


def test_discovery_output_is_summarized_into_a_direct_answer() -> None:
    observed: list[str | None] = []

    def fake_execute(
        text: str,
        session: ReplSession,
        console: Console,
        *,
        confirm_fn=None,
        is_tty=None,
    ) -> TerminalActionExecutionResult:
        session.last_command_observation = _OBSERVATION
        return TerminalActionExecutionResult(
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
        *,
        confirm_fn=None,
        is_tty=None,
        tool_observation: str | None = None,
    ) -> LlmRunInfo:
        observed.append(tool_observation)
        return LlmRunInfo(response_text="No — Sentry is not configured.")

    session = ReplSession()
    handle_message_with_agent(
        "is sentry installed?",
        session,
        _console(),
        recorder=None,
        on_exit=lambda: None,
        execute_actions=fake_execute,
        answer_agent=fake_answer,
        dispatch_command=_never_dispatch,
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
        *,
        confirm_fn=None,
        is_tty=None,
    ) -> TerminalActionExecutionResult:
        # No discovery observation recorded this turn.
        return TerminalActionExecutionResult(
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
        on_exit=lambda: None,
        execute_actions=fake_execute,
        answer_agent=fake_answer,
        dispatch_command=_never_dispatch,
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
        *,
        confirm_fn=None,
        is_tty=None,
    ) -> TerminalActionExecutionResult:
        session.last_command_observation = _OBSERVATION
        return TerminalActionExecutionResult(
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
        on_exit=lambda: None,
        execute_actions=fake_execute,
        answer_agent=fake_answer,
        dispatch_command=_never_dispatch,
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
        *,
        confirm_fn=None,
        is_tty=None,
    ) -> TerminalActionExecutionResult:
        # Does not set an observation this turn.
        return TerminalActionExecutionResult(
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
        on_exit=lambda: None,
        execute_actions=fake_execute,
        answer_agent=fake_answer,
        dispatch_command=_never_dispatch,
    )

    assert answer_calls == []
    assert session.last_command_observation is None
