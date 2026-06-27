"""ShellAgent lifecycle and boundary tests."""

from __future__ import annotations

import ast
import asyncio
import io
import threading
from pathlib import Path

import pytest
from rich.console import Console

from context.session import ReplSession
from interactive_shell.harness.agent import ShellAgent
from interactive_shell.harness.events import AgentEvent
from interactive_shell.runtime.core.confirmation import DispatchCancelled
from interactive_shell.runtime.core.turn_accounting import ToolCallingTurnResult


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=80)


def _handled(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
    return ToolCallingTurnResult(
        planned_count=1,
        executed_count=1,
        executed_success_count=1,
        has_unhandled_clause=False,
        handled=True,
        response_text="handled",
    )


def test_shell_agent_start_prompt_stop_lifecycle_events() -> None:
    async def _run() -> None:
        events: list[AgentEvent] = []
        agent = ShellAgent(
            ReplSession(),
            execute_actions=_handled,
            response_generator=lambda *_a, **_k: None,
            event_sink=events.append,
        )

        agent.start()
        result = await agent.prompt("run something", console=_console(), recorder=None)
        await agent.stop()

        assert result.final_intent == "cli_agent_handled"
        assert [event.type for event in events] == [
            "agent_start",
            "prompt_start",
            "prompt_end",
            "agent_stop",
        ]

    asyncio.run(_run())


def test_shell_agent_requires_start_before_prompt() -> None:
    async def _run() -> None:
        agent = ShellAgent(ReplSession(), execute_actions=_handled)

        with pytest.raises(RuntimeError, match="start"):
            await agent.prompt("run something", console=_console(), recorder=None)

    asyncio.run(_run())


def test_shell_agent_emits_interruption_event() -> None:
    async def _run() -> None:
        events: list[AgentEvent] = []

        def _cancelled(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
            raise DispatchCancelled()

        agent = ShellAgent(ReplSession(), execute_actions=_cancelled, event_sink=events.append)
        agent.start()

        with pytest.raises(DispatchCancelled):
            await agent.prompt("cancel me", console=_console(), recorder=None)

        assert [event.type for event in events] == [
            "agent_start",
            "prompt_start",
            "prompt_interrupted",
            "prompt_end",
        ]

    asyncio.run(_run())


def test_shell_agent_rejects_concurrent_prompt() -> None:
    async def _run() -> None:
        started = threading.Event()
        release = threading.Event()

        def _blocking(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
            started.set()
            assert release.wait(timeout=5)
            return _handled()

        agent = ShellAgent(ReplSession(), execute_actions=_blocking)
        agent.start()
        first = asyncio.create_task(agent.prompt("first", console=_console(), recorder=None))
        assert await asyncio.to_thread(started.wait, 5)

        with pytest.raises(RuntimeError, match="already processing"):
            await agent.prompt("second", console=_console(), recorder=None)

        release.set()
        await first

    asyncio.run(_run())


def test_shell_agent_lifecycle_modules_do_not_import_core_domain_or_orchestration() -> None:
    for module_name in ("agent.py", "agent_loop.py"):
        module_path = Path(__file__).parents[1] / module_name
        tree = ast.parse(module_path.read_text())

        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.append(node.module)

        assert not any(
            name.startswith(("core.domain", "tools.investigation")) for name in imports
        ), module_name
