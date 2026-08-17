"""Characterization of ``ActionTurnRunner`` and ``HeadlessAgent`` turn seams.

Pins the developer API after the nested-``def`` / long-kwargs cleanup:
the surface owns a runner, ``dispatch`` wires bound methods into ``run_turn``,
and ``bind_turn`` refreshes the runner when output or hooks change.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

from core.agent_harness.runtime import ActionTurnRunner, TurnBinding
from core.agent_harness.turns.headless_adapters import BufferOutputSink, NullToolProvider
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.execution import ToolExecutionHooks
from surfaces.interactive_shell.session import Session


def test_action_turn_runner_is_exported_from_runtime_not_the_root() -> None:
    from core import agent_harness as pkg
    from core.agent_harness import runtime

    assert runtime.ActionTurnRunner is ActionTurnRunner
    assert not hasattr(pkg, "ActionTurnRunner")
    assert not hasattr(runtime, "run_action_turn")
    assert not hasattr(runtime, "ActionRequest")
    assert not hasattr(runtime, "execute_action_agent_turn")


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

    agent.bind_turn(TurnBinding(output=second))
    after = agent._action_runner  # noqa: SLF001
    assert after is not before
    assert after.output is second


def test_bind_turn_rebuilds_action_runner_when_tool_hooks_change() -> None:
    hooks = ToolExecutionHooks()
    agent = HeadlessAgent(tools=NullToolProvider())
    before = agent._action_runner  # noqa: SLF001

    agent.bind_turn(TurnBinding(tool_hooks=hooks))
    after = agent._action_runner  # noqa: SLF001
    assert after is not before
    assert after.tool_hooks is hooks


def test_bind_turn_keeps_runner_when_only_accounting_changes() -> None:
    agent = HeadlessAgent(tools=NullToolProvider())
    before = agent._action_runner  # noqa: SLF001
    agent.bind_turn(TurnBinding(accounting=MagicMock()))
    assert agent._action_runner is before  # noqa: SLF001


def test_execute_shell_turn_adds_no_stage_of_its_own() -> None:
    """The shell drives the agent's own answer/gather/action stages by default.

    Answering and gathering are configured by the shell's ports (prompts, sink,
    reporter, gather progress/persist), not replaced. Only an injected test
    seam gets an adapter bound over it.
    """
    import io

    from rich.console import Console

    import surfaces.interactive_shell.runtime.shell_turn_execution as ste
    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent

    source = inspect.getsource(ste.execute_shell_turn)
    assert "build_shell_agent" in source
    assert "_ShellTurnBindings" not in source
    assert "ShellActionRunner" not in source

    agent = build_shell_agent(Session(), Console(file=io.StringIO(), force_terminal=False))
    ste._bind_injected_stages(  # noqa: SLF001
        agent,
        Session(),
        Console(file=io.StringIO()),
        BufferOutputSink(),
        execute_actions=None,
        answer_agent=None,
        gather_evidence=None,
        request_exit=None,
        tool_hooks=None,
    )
    assert agent._execute_actions_override is None  # noqa: SLF001
    assert agent._answer_override is None  # noqa: SLF001
    assert agent._gather_override is None  # noqa: SLF001


def test_shell_agent_keeps_core_runner_across_console_rebind() -> None:
    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent

    first = Console(file=MagicMock())
    second = Console(file=MagicMock())
    agent = build_shell_agent(Session(), first)
    before = agent._action_runner  # noqa: SLF001

    agent.bind_turn(TurnBinding(console=second))
    after = agent._action_runner  # noqa: SLF001

    assert after is before


def test_shell_agent_rebuilds_runner_when_external_output_changes() -> None:
    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent

    agent = build_shell_agent(Session(), Console(file=MagicMock()))
    before = agent._action_runner  # noqa: SLF001

    agent.bind_turn(TurnBinding(output=BufferOutputSink()))
    after = agent._action_runner  # noqa: SLF001

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
    agent.bind_turn(TurnBinding(tool_hooks=approval_hooks))
    assert agent._tool_hooks is approval_hooks  # noqa: SLF001

    # Act: the next turn's sink has no hooks.
    agent.bind_turn(TurnBinding(tool_hooks=None))

    # Assert
    assert agent._tool_hooks is None  # noqa: SLF001


def test_a_binding_states_the_whole_turn_and_replace_carries_it_forward() -> None:
    """A ``TurnBinding`` replaces the previous turn's values wholesale.

    Nothing accumulates from an earlier binding — hooks not in this turn's
    binding are gone. A host that rebinds mid-turn (the shell swaps accounting
    per goal-loop iteration) does so with ``replace(binding, …)``, which
    carries the turn's hooks and callback forward by construction.
    """
    from dataclasses import replace

    # Arrange
    agent = HeadlessAgent(tools=NullToolProvider())
    approval_hooks = MagicMock()
    confirm = MagicMock()
    binding = TurnBinding(tool_hooks=approval_hooks, confirm_fn=confirm, is_tty=True)
    agent.bind_turn(binding)

    # Act — a mid-turn rebind derived from the same binding keeps everything;
    # a fresh binding without hooks drops them.
    agent.bind_turn(replace(binding, accounting=MagicMock()))
    kept = (agent._tool_hooks, agent._confirm_fn, agent._is_tty)  # noqa: SLF001
    agent.bind_turn(TurnBinding(output=BufferOutputSink()))

    # Assert
    assert kept == (approval_hooks, confirm, True)
    assert agent._tool_hooks is None  # noqa: SLF001
    assert agent._confirm_fn is None  # noqa: SLF001


def test_long_lived_shell_agent_receives_each_turns_confirm_fn_and_tty() -> None:
    """A rebound turn's ``confirm_fn`` / ``is_tty`` reach the action stage.

    The REPL builds its agent once at startup (no confirm_fn) and rebinds it per
    turn. If the turn's confirmation callback were not rebound, a mutating
    ``cli_exec`` would fall back to blocking ``input()`` while prompt_toolkit
    owns stdin.
    """
    import io

    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent
    from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn

    # Arrange — agent built like the controller does: no confirm_fn, no is_tty.
    seen: list[tuple[Any, Any]] = []

    def _confirm(_prompt: str) -> str:
        return "y"

    def _spy_execute(text: str, session: Any, console: Any, **kwargs: Any) -> ToolCallingTurnResult:
        seen.append((kwargs.get("confirm_fn"), kwargs.get("is_tty")))
        return ToolCallingTurnResult(0, 0, 0, False, True)

    console = Console(file=io.StringIO(), force_terminal=False)
    agent = build_shell_agent(Session(), console)

    # Act — one turn on the long-lived agent, with this turn's confirm_fn / tty.
    execute_shell_turn(
        "run something",
        Session(),
        console,
        recorder=None,
        confirm_fn=_confirm,
        is_tty=True,
        agent=agent,
        execute_actions=_spy_execute,
        answer_agent=lambda *_a, **_k: None,
    )

    # Assert — the action stage saw the turn's callback, not the construction-time None.
    assert seen == [(_confirm, True)]


def test_shell_gather_progress_follows_the_turn_console_after_rebind() -> None:
    """The shell's gather progress renderer is a ConsoleBindable port the agent rebinds.

    The REPL streams every turn through a fresh spinner-aware console. Progress
    lines printed to the build-time console would land on the wrong stream, so
    ``bind_turn(console=…)`` must retarget the gather progress port too.
    """
    import io

    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent

    # Arrange — agent built on one console, turn bound to another.
    build_console = Console(file=io.StringIO(), force_terminal=False)
    turn_console = Console(file=io.StringIO(), force_terminal=False)
    agent = build_shell_agent(Session(), build_console)
    agent.bind_turn(TurnBinding(console=turn_console))

    # Act — the gather phase reports a tool start.
    on_progress = agent._gather_ports.on_progress  # noqa: SLF001
    assert on_progress is not None
    on_progress("tool_start", {"name": "query_grafana_metrics", "input": {"query": "up"}})

    # Assert — the line went to the turn console, not the build console.
    assert "checking" in turn_console.file.getvalue()  # type: ignore[attr-defined]
    assert build_console.file.getvalue() == ""  # type: ignore[attr-defined]


def test_a_stage_injected_on_one_turn_does_not_carry_into_the_next() -> None:
    """Injected seams are stated whole per turn, like ``TurnBinding``.

    On the long-lived REPL agent, a fake stage from an earlier turn must not
    stay active when a later turn omits it — omission means "the agent's own
    stage", never "whatever was bound before".
    """
    import io

    from rich.console import Console

    from surfaces.interactive_shell.runtime.shell_agent import build_shell_agent
    from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn

    # Arrange — a long-lived agent; turn 1 injects a fake action stage.
    console = Console(file=io.StringIO(), force_terminal=False)
    agent = build_shell_agent(Session(), console)

    def _fake_execute(text: str, session: Any, console: Any, **_kw: Any) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(0, 0, 0, False, True, response_text="fake")

    execute_shell_turn(
        "turn one",
        Session(),
        console,
        recorder=None,
        agent=agent,
        execute_actions=_fake_execute,
        answer_agent=lambda *_a, **_k: None,
    )
    assert agent._execute_actions_override is not None  # noqa: SLF001

    # Act — turn 2 omits the seam; bind only, do not dispatch (needs an LLM).
    from surfaces.interactive_shell.runtime import shell_turn_execution as ste

    ste._bind_injected_stages(  # noqa: SLF001
        agent,
        Session(),
        console,
        BufferOutputSink(),
        execute_actions=None,
        answer_agent=None,
        gather_evidence=None,
        request_exit=None,
        tool_hooks=None,
    )

    # Assert — the agent is back on its own action stage.
    assert agent._execute_actions_override is None  # noqa: SLF001
