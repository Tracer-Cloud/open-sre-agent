"""Tests for the agent slash-command tool.

Focus: agent-planned interactive picker/wizard commands must be deferred to the
REPL loop's exclusive-stdin path rather than run inline, where they would race
the live ``prompt_async()`` and leak terminal CPR replies (``ESC[row;colR``)
into the input line.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import pytest
from rich.console import Console

import tools.interactive_shell.actions.slash as slash_tool
from core.agent_harness.tools.tool_context import ActionToolContext
from surfaces.interactive_shell.session import Session


@dataclass
class FakeSlashPorts:
    """Controllable slash runtime adapter used by action-tool tests."""

    tty: bool = True
    dispatch_result: bool = True
    dispatched: list[str] = field(default_factory=list)

    def command_exists(self, _name: str) -> bool:
        return True

    def tty_interactive(self) -> bool:
        return self.tty

    def format_turn_outcome(self, command: str, *, ok: bool) -> str:
        status = "succeeded" if ok else "failed"
        return f"slash {command} ({status})"

    def execution_allowed(
        self,
        *,
        policy: Any,
        **_kwargs: Any,
    ) -> bool:
        del policy
        return True

    def dispatch(
        self,
        command: str,
        **_kwargs: Any,
    ) -> bool:
        self.dispatched.append(command)
        return self.dispatch_result


def _ctx(
    *,
    ports: FakeSlashPorts | None = None,
    request_exit: Any = None,
) -> tuple[ActionToolContext, io.StringIO, Session, FakeSlashPorts]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False)
    session = Session()
    resolved_ports = ports or FakeSlashPorts()

    return (
        ActionToolContext(
            session=session,
            console=console,
            request_exit=request_exit,
            slash_ports=resolved_ports,
        ),
        buf,
        session,
        resolved_ports,
    )


@pytest.mark.parametrize(
    ("command", "args", "expected"),
    [
        ("/integrations", ["remove", "github"], "/integrations remove github"),
        ("/integrations", ["setup", "datadog"], "/integrations setup datadog"),
        ("/mcp", ["connect", "github"], "/mcp connect github"),
        ("/mcp", ["disconnect", "github"], "/mcp disconnect github"),
        ("/integrations", [], "/integrations"),
        ("/mcp", [], "/mcp"),
    ],
)
def test_interactive_picker_command_is_deferred_to_exclusive_stdin(
    command: str,
    args: list[str],
    expected: str,
) -> None:
    """Picker commands are queued until the REPL owns exclusive stdin."""
    ctx, buf, session, ports = _ctx(ports=FakeSlashPorts(tty=True))

    handled = slash_tool.execute_slash_tool(
        {"command": command, "args": args},
        ctx,
    )

    assert handled is True
    assert ports.dispatched == []
    assert session.terminal.pending_prompt_default == expected
    assert session.terminal.pending_prompt_autosubmit is True
    assert session.history == []
    # Nothing is printed: the prefilled prompt line is the only announcement,
    # so the command is not shown twice before it runs.
    assert buf.getvalue() == ""


def test_interactive_picker_runs_inline_when_exclusive_stdin_active() -> None:
    """An already-exclusive turn must dispatch inline instead of re-queueing."""
    ctx, buf, session, ports = _ctx(ports=FakeSlashPorts(tty=True))
    session.terminal.exclusive_stdin_active = True

    handled = slash_tool.execute_slash_tool(
        {"command": "/integrations", "args": []},
        ctx,
    )

    assert handled is True
    assert ports.dispatched == ["/integrations"]
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False
    # Exclusive stdin means the user typed this slash literally, so the prompt
    # line already shows it — announcing it again would be the third rendering
    # of one command.
    assert buf.getvalue() == ""


def test_agent_resolved_slash_announces_itself() -> None:
    """Free text resolved into a slash has no prompt echo, so it must announce."""
    ctx, buf, session, ports = _ctx(ports=FakeSlashPorts(tty=True))

    slash_tool.execute_slash_tool({"command": "/health", "args": []}, ctx)

    assert ports.dispatched == ["/health"]
    assert "$ /health" in buf.getvalue()


def test_interactive_picker_runs_inline_when_not_a_tty() -> None:
    """Without an interactive TTY there is no live prompt to race."""
    ctx, _buf, session, ports = _ctx(ports=FakeSlashPorts(tty=False))

    slash_tool.execute_slash_tool(
        {
            "command": "/integrations",
            "args": ["remove", "github"],
        },
        ctx,
    )

    assert ports.dispatched == ["/integrations remove github"]
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False


def test_duplicate_slash_invoke_in_same_turn_is_ignored() -> None:
    """The same slash command must not execute twice in one agent turn."""
    ctx, _buf, _session, ports = _ctx(ports=FakeSlashPorts(tty=True))
    args = {"command": "/integrations", "args": ["list"]}

    assert slash_tool.execute_slash_tool(args, ctx) is True
    assert slash_tool.execute_slash_tool(args, ctx) is True

    assert ports.dispatched == ["/integrations list"]


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("/integrations", ["list"]),
        ("/integrations", ["show", "github"]),
        ("/health", []),
    ],
)
def test_non_picker_slash_commands_run_inline_even_in_a_tty(
    command: str,
    args: list[str],
) -> None:
    """Commands that do not read raw stdin continue to run inline."""
    ctx, _buf, session, ports = _ctx(ports=FakeSlashPorts(tty=True))

    slash_tool.execute_slash_tool(
        {"command": command, "args": args},
        ctx,
    )

    expected = " ".join([command, *args]) if args else command
    assert ports.dispatched == [expected]
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False


def test_exit_slash_requests_runtime_exit() -> None:
    ports = FakeSlashPorts(dispatch_result=False)
    requested_exit: list[bool] = []

    ctx, _buf, _session, _ports = _ctx(
        ports=ports,
        request_exit=lambda: requested_exit.append(True),
    )

    handled = slash_tool.execute_slash_tool(
        {"command": "/quit", "args": []},
        ctx,
    )

    assert handled is True
    assert ports.dispatched == ["/quit"]
    assert requested_exit == [True]
