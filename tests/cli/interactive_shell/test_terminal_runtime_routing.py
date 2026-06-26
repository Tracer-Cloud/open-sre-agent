"""Routing-focused tests for interactive shell terminal runtime dispatch helpers."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions import (
    TerminalActionExecutionResult,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    LlmActionPlanResult,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tools import (
    investigation_tool as _investigation_tool,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tools import (
    slash_tool as _slash_tool,
)
from cli.interactive_shell.routing.types import RouteKind
from cli.interactive_shell.runtime import dispatch as loop_dispatch
from cli.interactive_shell.runtime import execution as loop_execution
from cli.interactive_shell.runtime.session import ReplSession


def test_dispatch_one_turn_typoed_bare_alias_dispatches_canonical_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare-alias typos (e.g. ``hlep`` → ``/help``) normalize before slash dispatch."""
    dispatched: list[str] = []

    def _dispatch(command: str, *_args: object, **_kwargs: object) -> bool:
        dispatched.append(command)
        return True

    monkeypatch.setattr(loop_execution, "dispatch_slash", _dispatch)
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    loop_dispatch.dispatch_one_turn("hlep", session, console, on_exit=lambda: None)

    assert dispatched == ["/help"]


def test_dispatch_one_turn_bare_integrations_alias_preserves_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []

    def _dispatch(command: str, *_args: object, **_kwargs: object) -> bool:
        dispatched.append(command)
        return True

    monkeypatch.setattr(loop_execution, "dispatch_slash", _dispatch)
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    loop_dispatch.dispatch_one_turn("integrations list", session, console, on_exit=lambda: None)

    assert dispatched == ["/integrations list"]


def test_dispatch_needs_exclusive_stdin_for_bare_integration_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_dispatch, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_dispatch.dispatch_needs_exclusive_stdin("/integrations", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("integrations", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/investigate", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/mcp", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/model", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/theme", session) is True

    assert loop_dispatch.dispatch_needs_exclusive_stdin("/integrations list", session) is False
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/theme blue", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("integrations list", session) is False


def test_dispatch_needs_exclusive_stdin_false_for_investigate_with_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued menu selections run as ``/investigate <target>`` without blocking the prompt."""
    monkeypatch.setattr(loop_dispatch, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_dispatch.dispatch_needs_exclusive_stdin("/investigate generic", session) is False
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/investigate alert.json", session) is False
    assert loop_dispatch.dispatch_needs_exclusive_stdin("investigate generic", session) is False


def test_dispatch_needs_exclusive_stdin_for_exit_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_dispatch, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_dispatch.dispatch_needs_exclusive_stdin("/exit", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("quit", session) is True


def test_dispatch_needs_exclusive_stdin_for_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/update`` hits the network; block the next prompt until output is printed."""
    monkeypatch.setattr(loop_dispatch, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_dispatch.dispatch_needs_exclusive_stdin("/update", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("update", session) is True


def test_dispatch_needs_exclusive_stdin_for_integration_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_dispatch, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_dispatch.dispatch_needs_exclusive_stdin("/integrations setup", session) is True
    assert (
        loop_dispatch.dispatch_needs_exclusive_stdin("integrations setup datadog", session) is True
    )
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/mcp connect github", session) is True


def test_dispatch_needs_exclusive_stdin_for_integration_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``remove``/``disconnect`` drive a native inline picker that reads raw
    stdin; the REPL must block the next prompt so keystrokes and CPR responses
    do not leak into the prompt buffer."""
    monkeypatch.setattr(loop_dispatch, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_dispatch.dispatch_needs_exclusive_stdin("/integrations remove", session) is True
    assert (
        loop_dispatch.dispatch_needs_exclusive_stdin("/integrations remove github", session) is True
    )
    assert loop_dispatch.dispatch_needs_exclusive_stdin("integrations remove github", session) is (
        True
    )
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/mcp disconnect", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/mcp disconnect github", session) is True


def test_dispatch_needs_exclusive_stdin_for_onboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/onboard`` is an interactive wizard; the REPL must wait for it to
    finish before reading the next prompt so the wizard subprocess has
    exclusive stdin and can drive its own questionary widgets.
    """
    monkeypatch.setattr(loop_dispatch, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_dispatch.dispatch_needs_exclusive_stdin("/onboard", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("onboard", session) is True
    # Args don't change the exclusive-stdin requirement.
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/onboard local_llm", session) is True


def test_dispatch_needs_exclusive_stdin_for_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/config`` delegates to a subprocess; block the next prompt until output
    is printed so config lines do not overlap the pinned input bar.
    """
    monkeypatch.setattr(loop_dispatch, "repl_tty_interactive", lambda: True)
    session = ReplSession()

    assert loop_dispatch.dispatch_needs_exclusive_stdin("/config", session) is True
    assert loop_dispatch.dispatch_needs_exclusive_stdin("/config show", session) is True
    assert (
        loop_dispatch.dispatch_needs_exclusive_stdin(
            "/config set interactive.layout pinned",
            session,
        )
        is True
    )


def test_dispatch_one_turn_nitro_prompt_uses_cli_agent_actions(
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
        confirm_fn=None,
        is_tty=None,
    ) -> TerminalActionExecutionResult:
        _ = confirm_fn, is_tty
        action_calls.append(text)
        return TerminalActionExecutionResult(
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
        confirm_fn=None,
    ) -> None:
        _ = confirm_fn
        llm_calls.append(text)

    monkeypatch.setattr(
        loop_execution,
        "execute_cli_actions",
        _fake_execute_cli_actions,
    )
    monkeypatch.setattr(loop_execution, "answer_cli_agent", _fake_answer_cli_agent)

    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    loop_dispatch.dispatch_one_turn(nitro_prompt, session, console, on_exit=lambda: None)

    assert action_calls == [nitro_prompt]
    assert llm_calls == []
    assert session.last_route_decision is not None
    assert session.last_route_decision.route_kind is RouteKind.HANDLE_MESSAGE_WITH_AGENT


def test_dispatch_one_turn_nitro_prompt_executes_remote_then_investigation(
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
        "cli.interactive_shell.routing.handle_message_with_agent.orchestration"
        ".terminal_actions.planning.plan_actions_with_llm_result",
        lambda _message, *, session=None: LlmActionPlanResult(  # noqa: ARG005
            actions=(
                PlannedAction(kind="slash", content="/remote", position=0),
                PlannedAction(kind="investigation", content="hello world", position=1),
            ),
            has_unhandled_clause=False,
            policy_trace=("fake_planner",),
        ),
    )
    monkeypatch.setattr(_slash_tool, "dispatch_slash", _fake_dispatch)
    monkeypatch.setattr(_investigation_tool, "run_text_investigation", _fake_run_text_investigation)

    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    loop_dispatch.dispatch_one_turn(nitro_prompt, session, console, on_exit=lambda: None)

    assert call_order == ["slash:/remote", "investigation:hello world"]
    assert session.last_route_decision is not None
    assert session.last_route_decision.route_kind is RouteKind.HANDLE_MESSAGE_WITH_AGENT


class TestDispatchSpinnerRouting:
    @pytest.mark.parametrize(
        "text",
        [
            "/history",
            "/tests",
            "/model show",
            "tests",
            "help",
            # The agent fast path typo-corrects single-edit bare aliases before dispatch.
            "testts",
            "hlep",
            "opensre investigate -i alert.json",
        ],
    )
    def test_slash_dispatches_do_not_show_assistant_spinner(self, text: str) -> None:
        assert loop_dispatch.dispatch_should_show_spinner(text, ReplSession()) is False

    @pytest.mark.parametrize(
        "text",
        [
            "why did this fail?",
            "explain deploy",
        ],
    )
    def test_non_slash_dispatches_show_assistant_spinner(self, text: str) -> None:
        assert loop_dispatch.dispatch_should_show_spinner(text, ReplSession()) is True
