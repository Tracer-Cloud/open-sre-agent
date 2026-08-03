"""Characterization of ``ActionTurnRunner`` and ``HeadlessAgent`` turn seams.

Pins the developer API after the nested-``def`` / long-kwargs cleanup:
the surface owns a runner, ``dispatch`` wires bound methods into ``run_turn``,
and ``bind_turn`` refreshes the runner when output or hooks change.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

from core.agent_harness import ActionTurnRunner
from core.agent_harness.turns.headless_adapters import BufferOutputSink, NullToolProvider
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.session import Session


def test_action_turn_runner_is_package_export() -> None:
    from core import agent_harness as pkg

    assert pkg.ActionTurnRunner is ActionTurnRunner
    assert not hasattr(pkg, "run_action_turn")
    assert not hasattr(pkg, "ActionRequest")
    assert not hasattr(pkg, "execute_action_agent_turn")


def test_action_turn_runner_run_accepts_confirm_fn(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(message: str, session: Any, args: Any) -> ToolCallingTurnResult:
        captured["message"] = message
        captured["confirm_fn"] = args.confirm_fn
        captured["is_tty"] = args.is_tty
        return ToolCallingTurnResult(0, 0, 0, False, False)

    monkeypatch.setattr(
        "core.agent_harness.turns.action_driver._run_action_turn",
        _fake_run,
    )

    def _confirm(_prompt: str) -> str:
        return "y"

    result = ActionTurnRunner(
        output=BufferOutputSink(),
        tools=NullToolProvider(),
    ).run("hi", Session(), confirm_fn=_confirm, is_tty=False)

    assert result.handled is False
    assert captured["message"] == "hi"
    assert captured["confirm_fn"] is _confirm
    assert captured["is_tty"] is False


def test_headless_dispatch_uses_bound_methods_not_nested_defs() -> None:
    source = inspect.getsource(HeadlessAgent.dispatch)
    assert "def execute_actions" not in source
    assert "def answer" not in source
    assert "def gather" not in source
    assert "self._execute_actions" in source
    assert "self._answer" in source
    assert "self._gather" in source


def test_bind_turn_rebuilds_action_runner_when_output_changes() -> None:
    first = BufferOutputSink()
    second = BufferOutputSink()
    agent = HeadlessAgent(tools=NullToolProvider(), output=first)
    before = agent._action_runner  # noqa: SLF001
    assert before.output is first

    agent.bind_turn(output=second)
    after = agent._action_runner  # noqa: SLF001
    assert after is not before
    assert after.output is second


def test_bind_turn_rebuilds_action_runner_when_tool_hooks_change() -> None:
    hooks = ToolExecutionHooks()
    agent = HeadlessAgent(tools=NullToolProvider())
    before = agent._action_runner  # noqa: SLF001

    agent.bind_turn(tool_hooks=hooks)
    after = agent._action_runner  # noqa: SLF001
    assert after is not before
    assert after.tool_hooks is hooks


def test_bind_turn_keeps_runner_when_only_accounting_changes() -> None:
    agent = HeadlessAgent(tools=NullToolProvider())
    before = agent._action_runner  # noqa: SLF001
    agent.bind_turn(accounting=MagicMock())
    assert agent._action_runner is before  # noqa: SLF001


def test_execute_shell_turn_binds_via_named_bindings_type() -> None:
    import surfaces.interactive_shell.runtime.shell_turn_execution as ste

    source = inspect.getsource(ste.execute_shell_turn)
    assert "def execute_bound" not in source
    assert "def answer_bound" not in source
    assert "def gather_bound" not in source
    assert "_ShellTurnBindings" in source
    assert "action_runner" in source
    assert hasattr(ste._ShellTurnBindings, "execute_actions")
    assert hasattr(ste._ShellTurnBindings, "answer_question")
    assert hasattr(ste._ShellTurnBindings, "gather_evidence")


def test_shell_action_runner_keeps_core_runner_across_console_rebind() -> None:
    from rich.console import Console

    from surfaces.interactive_shell.runtime.action_turn import ShellActionRunner

    first = Console(file=MagicMock())
    second = Console(file=MagicMock())
    runner = ShellActionRunner(session=Session(), console=first)
    before = runner._action_runner  # noqa: SLF001

    runner.bind_turn(console=second)
    after = runner._action_runner  # noqa: SLF001

    assert after is before
    assert runner._console is second  # noqa: SLF001


def test_shell_action_runner_rebuilds_when_external_output_changes() -> None:
    from rich.console import Console

    from surfaces.interactive_shell.runtime.action_turn import ShellActionRunner

    console = Console(file=MagicMock())
    runner = ShellActionRunner(session=Session(), console=console)
    before = runner._action_runner  # noqa: SLF001

    runner.bind_turn(output=BufferOutputSink())
    after = runner._action_runner  # noqa: SLF001

    assert after is not before


def test_a_sink_without_hooks_clears_the_previous_sinks_hooks() -> None:
    """A pooled agent must not carry approval hooks between conversations.

    The gateway reuses agents from a pool and binds each turn with
    ``tool_hooks=getattr(sink, "tool_hooks", None)``. A sink with no hooks
    therefore passes ``None``. If that were read as "leave them alone", the
    previous sink's approval callback would stay attached and later tool calls
    would send approval controls to the wrong conversation — or wait on a reply
    that can never arrive.
    """
    # Arrange
    agent = HeadlessAgent(tools=NullToolProvider())
    approval_hooks = MagicMock()
    agent.bind_turn(tool_hooks=approval_hooks)
    assert agent._tool_hooks is approval_hooks  # noqa: SLF001

    # Act: the next turn's sink has no hooks.
    agent.bind_turn(tool_hooks=None)

    # Assert
    assert agent._tool_hooks is None  # noqa: SLF001


def test_omitting_hooks_leaves_them_attached() -> None:
    """Rebinding only the sink must not silently drop the hooks.

    The mirror of the case above: callers that swap ``output`` alone rely on the
    hooks surviving, so "not mentioned" and "explicitly cleared" cannot be the
    same thing.
    """
    # Arrange
    agent = HeadlessAgent(tools=NullToolProvider())
    approval_hooks = MagicMock()
    agent.bind_turn(tool_hooks=approval_hooks)

    # Act
    agent.bind_turn(output=BufferOutputSink())

    # Assert
    assert agent._tool_hooks is approval_hooks  # noqa: SLF001
