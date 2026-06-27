"""Turn-focused tests for interactive shell terminal runtime dispatch helpers."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from context.session import ReplSession
from core.runtime.llm.agent_llm_client import AgentLLMResponse, ToolCall
from interactive_shell.agent_shell.agent import handle_message_with_agent
from interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from interactive_shell.runtime.utils import input_policy as loop_input_policy
from interactive_shell.tools import (
    investigation_tool as _investigation_tool,
)
from interactive_shell.tools import (
    slash_tool as _slash_tool,
)
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
)


def test_turn_needs_exclusive_stdin_for_bare_integration_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/investigate", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/model", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/theme", session) is True

    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations list", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("/theme blue", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/verify", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/verify datadog", session) is False

    # Gating is literal-/slash only: bare command words are not recognized.
    assert loop_input_policy.turn_needs_exclusive_stdin("integrations", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("integrations list", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("verify", session) is False


def test_turn_needs_exclusive_stdin_false_for_investigate_with_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued menu selections run as ``/investigate <target>`` without blocking the prompt."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_input_policy.turn_needs_exclusive_stdin("/investigate generic", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("/investigate alert.json", session) is False


def test_turn_needs_exclusive_stdin_for_exit_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_input_policy.turn_needs_exclusive_stdin("/exit", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/quit", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert loop_input_policy.turn_needs_exclusive_stdin("quit", session) is False


def test_turn_needs_exclusive_stdin_for_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/update`` hits the network; block the next prompt until output is printed."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_input_policy.turn_needs_exclusive_stdin("/update", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert loop_input_policy.turn_needs_exclusive_stdin("update", session) is False


def test_turn_needs_exclusive_stdin_for_integration_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations setup", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp connect github", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert (
        loop_input_policy.turn_needs_exclusive_stdin("integrations setup datadog", session) is False
    )


def test_turn_needs_exclusive_stdin_for_integration_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``remove``/``disconnect`` drive a native inline picker that reads raw
    stdin; the REPL must block the next prompt so keystrokes and CPR responses
    do not leak into the prompt buffer."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations remove", session) is True
    assert (
        loop_input_policy.turn_needs_exclusive_stdin("/integrations remove github", session) is True
    )
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp disconnect", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp disconnect github", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert (
        loop_input_policy.turn_needs_exclusive_stdin("integrations remove github", session) is False
    )


def test_turn_needs_exclusive_stdin_for_onboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/onboard`` is an interactive wizard; the REPL must wait for it to
    finish before reading the next prompt so the wizard subprocess has
    exclusive stdin and can drive its own questionary widgets.
    """
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_input_policy.turn_needs_exclusive_stdin("/onboard", session) is True
    # Args don't change the exclusive-stdin requirement.
    assert loop_input_policy.turn_needs_exclusive_stdin("/onboard local_llm", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert loop_input_policy.turn_needs_exclusive_stdin("onboard", session) is False


def test_turn_needs_exclusive_stdin_for_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/config`` delegates to a subprocess; block the next prompt until output
    is printed so config lines do not overlap the pinned input bar.
    """
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_input_policy.turn_needs_exclusive_stdin("/config", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/config show", session) is True
    assert (
        loop_input_policy.turn_needs_exclusive_stdin(
            "/config set interactive.layout pinned",
            session,
        )
        is True
    )


def test_handle_message_with_agent_nitro_prompt_uses_cli_agent_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nitro_prompt = (
        "I want to deploy OpenSRE on a remote EC2 Nitro instance, and then I want to send\n"
        'it an investigation. Can you please deploy the instance and send it "hello world"?'
    )
    action_calls: list[str] = []
    llm_calls: list[str] = []

    def _fake_execute_cli_actions(
        text: str,
        _session: ReplSession,
        _console: Console,
        **kwargs: object,
    ) -> ToolCallingTurnResult:
        action_calls.append(text)
        return ToolCallingTurnResult(
            planned_count=2,
            executed_count=2,
            executed_success_count=2,
            has_unhandled_clause=False,
            handled=True,
        )

    def _fake_answer_cli_agent(
        text: str,
        _session: ReplSession,
        _console: Console,
        **kwargs: object,
    ) -> None:
        llm_calls.append(text)

    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    handle_message_with_agent(
        nitro_prompt,
        session,
        console,
        recorder=None,
        confirm_fn=None,
        is_tty=None,
        execute_actions=_fake_execute_cli_actions,
        answer_agent=_fake_answer_cli_agent,
    )

    assert action_calls == [nitro_prompt]
    assert llm_calls == []


def test_handle_message_with_agent_nitro_prompt_executes_remote_then_investigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nitro_prompt = (
        "I want to deploy OpenSRE on a remote EC2 Nitro instance, and then I want to send\n"
        'it an investigation. Can you please deploy the instance and send it "hello world"?'
    )
    call_order: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        call_order.append(f"slash:{command}")
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    def _fake_run_text_investigation(
        alert_text: str,
        _session: ReplSession,
        _console: Console,
        **_kwargs: object,
    ) -> None:
        call_order.append(f"investigation:{alert_text}")

    monkeypatch.setattr(
        "interactive_shell.agent_shell.tool_calling._default_llm_factory",
        lambda: FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_remote",
                            name="slash_invoke",
                            input={"command": "/remote", "args": []},
                        ),
                        ToolCall(
                            id="call_investigate",
                            name="investigation_start",
                            input={"alert_text": "hello world"},
                        ),
                    ],
                    raw_content=None,
                )
            ]
        ),
    )
    monkeypatch.setattr(_slash_tool, "dispatch_slash", _fake_dispatch)
    monkeypatch.setattr(_investigation_tool, "run_text_investigation", _fake_run_text_investigation)

    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    handle_message_with_agent(
        nitro_prompt,
        session,
        console,
        recorder=None,
        confirm_fn=None,
        is_tty=None,
    )

    assert call_order == ["slash:/remote", "investigation:hello world"]


class TestDispatchSpinnerBehavior:
    @pytest.mark.parametrize(
        "text",
        [
            "/history",
            "/tests",
            "/model show",
        ],
    )
    def test_slash_dispatches_do_not_show_assistant_spinner(self, text: str) -> None:
        assert loop_input_policy.turn_should_show_spinner(text, ReplSession()) is False

    @pytest.mark.parametrize(
        "text",
        [
            "why did this fail?",
            "explain deploy",
            # Bare command words and opensre passthrough are no longer treated as
            # literal commands, so the spinner shows while the planner runs.
            "tests",
            "help",
            "opensre investigate -i alert.json",
        ],
    )
    def test_non_slash_dispatches_show_assistant_spinner(self, text: str) -> None:
        assert loop_input_policy.turn_should_show_spinner(text, ReplSession()) is True
