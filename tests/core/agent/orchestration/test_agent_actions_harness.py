"""Action-execution tests over model tool calls, not planner DTOs."""

from __future__ import annotations

from collections.abc import Iterable

from rich.console import Console

import tools.interactive_shell.actions.slash as slash_tool
from core.agent_harness.agents.action_agent import (
    ActionTurnPlan,
    ToolCallingDeps,
    _build_action_agent,
    run_action_agent_turn,
)
from core.agent_harness.session import Session
from core.tool_framework.registered_tool import RegisteredTool
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


class _GenericActionToolProvider:
    def __init__(self, tool: RegisteredTool) -> None:
        self._tool = tool

    def action_tools(self, **_kwargs: object) -> list[RegisteredTool]:
        return [self._tool]

    def observer(self, **_kwargs: object):
        return lambda _kind, _data: None


class _OutputSink:
    def __init__(self, console: Console) -> None:
        self._console = console

    def print(self, message: str = "") -> None:
        self._console.print(message)

    def render_response_header(self, label: str) -> None:
        self._console.print(label)

    def render_error(self, message: str) -> None:
        self._console.print(message)

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        text = "".join(chunks)
        self._console.print(text)
        return text


def test_execute_with_harness_runs_slash_tool_call(monkeypatch) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    harness = ActionExecutionHarness(
        llm=FakeActionLLM([tool_response("slash_invoke", {"command": "/health", "args": []})])
    )
    session = Session()

    result = run_action_tool_turn(
        "check health",
        session,
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 1
    assert dispatched == ["/health"]
    assert "slash_invoke" in harness.llm.tool_schema_names


def test_generic_registered_action_tool_result_marks_turn_handled() -> None:
    tool = RegisteredTool(
        name="fake_send_message",
        description="Send a fake message.",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        source="knowledge",
        surfaces=("action",),
        run=lambda message: {"status": "sent", "message": message},
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM([tool_response("fake_send_message", {"message": "hello"})])
    )

    result = run_action_agent_turn(
        "send a fake message",
        Session(),
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
        is_tty=False,
    )

    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 1
    assert result.executed_success_count == 1
    assert 'fake_send_message input: {"message": "hello"}' in result.response_text
    assert '"status": "sent"' in result.response_text
    assert "fake_send_message" in harness.llm.tool_schema_names


def test_literal_slash_command_dispatches_deterministically_without_llm(
    monkeypatch,
) -> None:
    """A literal ``/command`` typed by the user dispatches via ``slash_invoke``
    without consulting the action-agent LLM, so slash commands keep working when
    the LLM is unavailable (e.g. a provider with no credit)."""
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))
    session = Session()

    result = run_action_tool_turn(
        "/sessions",
        session,
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert result.planned_count == 1
    assert dispatched == ["/sessions"]
    assert session.history == [{"type": "slash", "text": "/sessions", "ok": True}]
    # The deterministic path must not consult the action-agent LLM.
    assert harness.llm.invocations == 0


def test_literal_slash_command_forwards_args_without_llm(monkeypatch) -> None:
    """``/login chatgpt`` dispatches with its positional args and no LLM call."""
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))

    result = run_action_tool_turn(
        "/login chatgpt",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert dispatched == ["/login chatgpt"]
    assert harness.llm.invocations == 0


def test_natural_language_still_routes_through_action_agent(monkeypatch) -> None:
    """Non-slash, free-form text is still selected by the action-agent LLM —
    the deterministic path is limited to literal ``/command`` input."""

    def _unexpected_dispatch(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("free-form text must not deterministically dispatch a slash command")

    monkeypatch.setattr(slash_tool, "dispatch_slash", _unexpected_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))

    result = run_action_tool_turn(
        "log me in please",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert harness.llm.invocations == 1
    assert result.handled is False


def test_execute_with_harness_hands_off_handoff_only_tool_call() -> None:
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [tool_response("assistant_handoff", {"content": "docs:help"})],
        )
    )

    result = run_action_tool_turn(
        "half actionable prompt",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is False
    assert result.has_unhandled_clause is False
    assert result.planned_count == 0
    assert "Requested actions" not in harness.console_buffer.getvalue()


def test_execute_with_harness_handles_llm_unavailable() -> None:
    def _raise() -> object:
        raise RuntimeError("action agent unavailable")

    session = Session()
    result = run_action_tool_turn(
        "action agent outage",
        session,
        Console(force_terminal=False),
        deps=ToolCallingDeps(llm_factory=_raise),
    )

    assert result.handled is True
    assert result.has_unhandled_clause is True
    assert result.planned_count == 0
    assert session.cli_agent_messages[-1] == ("assistant", "action agent unavailable")


def test_build_action_agent_returns_action_turn_plan() -> None:
    llm = FakeActionLLM([no_tool_response()])
    deps = ToolCallingDeps(llm_factory=lambda: llm)
    session = Session()

    plan = _build_action_agent(
        message="test message",
        session=session,
        agent_tools=[],
        turn_snapshot=None,
        deps=deps,
        tool_hooks=None,
        tool_resources={},
        observer=lambda *_args, **_kwargs: None,
    )

    assert isinstance(plan, ActionTurnPlan)
    assert "test message" in plan.user_message
    assert plan.agent is not None
