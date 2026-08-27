"""Turn-focused tests for interactive shell terminal runtime dispatch helpers."""

from __future__ import annotations

import asyncio
import contextlib
import io

import pytest
from rich.console import Console

import surfaces.interactive_shell.runtime.slash_adapter as slash_adapter
from core.llm.types import AgentLLMResponse, ToolCall
from surfaces.interactive_shell.runtime import input_policy as loop_input_policy
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.runtime.core.turn_accounting import (
    ToolCallingTurnResult,
)
from surfaces.interactive_shell.runtime.turn_host import (
    AgentTurnResources,
    run_agent_turn,
    run_agent_turn_queue,
)
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
)
from tests.shared.harness_turn_driver import run_harness_turn
from tools.interactive_shell.actions import (
    investigation as _investigation_tool,
)


def test_turn_needs_exclusive_stdin_for_bare_integration_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/investigate", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/mcp", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/memory", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/model", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/loops", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/theme", session) is True

    assert loop_input_policy.turn_needs_exclusive_stdin("/integrations list", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("/loops active", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/loops messages", session) is True
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
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/investigate generic", session) is False
    assert loop_input_policy.turn_needs_exclusive_stdin("/investigate alert.json", session) is False


def test_turn_needs_exclusive_stdin_for_exit_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/exit", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/quit", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert loop_input_policy.turn_needs_exclusive_stdin("quit", session) is False


def test_turn_needs_exclusive_stdin_for_goal_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/goal set`` must finish before the condition autosubmits as ``[N] ❯``."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()
    assert (
        loop_input_policy.turn_needs_exclusive_stdin(
            "/goal set --max-turns 4 count windows users",
            session,
        )
        is True
    )
    assert loop_input_policy.turn_needs_exclusive_stdin("/goal", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("goal set x", session) is False


def test_turn_needs_exclusive_stdin_for_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/update`` hits the network; block the next prompt until output is printed."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/update", session) is True
    # Bare command words are not recognized under literal-/slash gating.
    assert loop_input_policy.turn_needs_exclusive_stdin("update", session) is False


def test_turn_needs_exclusive_stdin_for_integration_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

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
    session = Session()

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


def test_turn_needs_exclusive_stdin_for_background_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/background`` status/list/show print Rich tables; exclusive stdin keeps
    the next ``prompt_async()`` from racing the table render and leaking CPR
    bytes into the prompt buffer. Mutating forms like ``on`` stay ungated."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/background", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/background status", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/background list", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/background show", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/background show abc12", session) is True

    assert loop_input_policy.turn_needs_exclusive_stdin("/background on", session) is False
    # Bare command words are not recognized under literal-/slash gating.
    assert loop_input_policy.turn_needs_exclusive_stdin("background", session) is False


def test_turn_needs_exclusive_stdin_for_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/health`` prints the Integration Checks Rich table."""
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/health", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("health", session) is False


def test_turn_needs_exclusive_stdin_for_onboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/onboard`` is an interactive wizard; the REPL must wait for it to
    finish before reading the next prompt so the wizard subprocess has
    exclusive stdin and can drive its own questionary widgets.
    """
    monkeypatch.setattr(loop_input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/onboard", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/setup", session) is True
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
    session = Session()

    assert loop_input_policy.turn_needs_exclusive_stdin("/config", session) is True
    assert loop_input_policy.turn_needs_exclusive_stdin("/config show", session) is True
    assert (
        loop_input_policy.turn_needs_exclusive_stdin(
            "/config set interactive.layout pinned",
            session,
        )
        is True
    )


@pytest.mark.asyncio
async def test_queued_literal_quit_requests_runtime_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued ``/quit`` must set ``exit_requested`` without blocking on analytics I/O."""
    # Match ``test_commands.py``: real ``/quit`` can flush PostHog; under xdist +
    # coverage that network drain has hung CI workers for the full job timeout.
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.system._flush_analytics_on_exit",
        lambda _console: None,
    )
    from surfaces.interactive_shell.runtime.core.state import ReplState

    state = ReplState()
    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    async def _run_turn(text: str) -> None:
        await asyncio.to_thread(
            run_harness_turn,
            text,
            session,
            console,
            recorder=None,
            confirm_fn=None,
            is_tty=None,
            request_exit=state.request_exit,
        )

    worker = asyncio.create_task(run_agent_turn_queue(state=state, run_turn=_run_turn))
    try:
        await state.queue.put("/quit")
        # Generous under loaded test-cov + xdist (see test_terminal_runtime.py).
        await asyncio.wait_for(state.queue.join(), timeout=5.0)
        await asyncio.wait_for(worker, timeout=5.0)
    finally:
        if not worker.done():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

    assert state.exit_requested is True


def test_turn_end_retries_auto_command_deferred_during_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ask-user ``/choose`` queued mid-turn must wake the open prompt."""

    async def _scenario() -> None:
        from surfaces.interactive_shell.runtime import shell_turn_execution

        session = Session()
        refresh_dispatch_states: list[bool] = []
        session.terminal.prompt_refresh_fn = lambda: refresh_dispatch_states.append(
            session.terminal.dispatch_active
        )

        def _queue_choose(*_args: object, **_kwargs: object) -> None:
            session.terminal.set_auto_command("/choose")

        monkeypatch.setattr(shell_turn_execution, "execute_shell_turn", _queue_choose)
        runtime = AgentTurnResources(
            session=session,
            state=ReplState(),
            spinner=SpinnerState(),
            invalidate_prompt=lambda: None,
            console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
        )

        await run_agent_turn(runtime, "ask me to choose")

        assert session.terminal.pending_prompt_default == "/choose"
        assert refresh_dispatch_states == [True, False]

    asyncio.run(_scenario())


def test_run_harness_turn_nitro_prompt_uses_cli_agent_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nitro_prompt = (
        "I want to deploy OpenSRE on a remote EC2 Nitro instance, and then I want to send\n"
        'it an investigation. Can you please deploy the instance and send it "hello world"?'
    )
    action_calls: list[str] = []

    def _fake_execute_cli_actions(
        text: str,
        _session: Session,
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

    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    run_harness_turn(
        nitro_prompt,
        session,
        console,
        recorder=None,
        confirm_fn=None,
        is_tty=None,
        execute_actions=_fake_execute_cli_actions,
    )

    assert action_calls == [nitro_prompt]


def test_run_harness_turn_nitro_prompt_executes_remote_then_investigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nitro_prompt = (
        "I want to deploy OpenSRE on a remote EC2 Nitro instance, and then I want to send\n"
        'it an investigation. Can you please deploy the instance and send it "hello world"?'
    )
    call_order: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        call_order.append(f"slash:{command}")
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    def _fake_run_text_investigation(
        alert_text: str,
        _session: Session,
        _console: Console,
        **_kwargs: object,
    ) -> None:
        call_order.append(f"investigation:{alert_text}")

    monkeypatch.setattr(
        "core.agent_harness.turns.headless_build.default_llm_factory",
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
    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)
    monkeypatch.setattr(_investigation_tool, "run_text_investigation", _fake_run_text_investigation)

    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    run_harness_turn(
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
        assert loop_input_policy.turn_should_show_spinner(text, Session()) is False

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
        assert loop_input_policy.turn_should_show_spinner(text, Session()) is True
