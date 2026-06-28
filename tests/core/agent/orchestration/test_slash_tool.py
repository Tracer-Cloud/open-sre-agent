"""Tests for the agent slash-command tool.

Focus: agent-planned interactive picker/wizard commands must be deferred to the
REPL loop's exclusive-stdin path rather than run inline, where they would race
the live ``prompt_async()`` and leak terminal CPR replies (``ESC[row;colR``)
into the input line.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

import tools.interactive_shell.actions.slash as slash_tool
from core.agent_harness.session import ReplSession
from tools.interactive_shell.contracts import (
    ToolContext,
)


def _ctx() -> tuple[ToolContext, io.StringIO, ReplSession]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False)
    session = ReplSession()
    return ToolContext(session=session, console=console), buf, session


def _record_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    dispatched: list[str] = []

    def _fake_dispatch(command: str, *_args: object, **_kwargs: object) -> bool:
        dispatched.append(command)
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    return dispatched


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
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    args: list[str],
    expected: str,
) -> None:
    """A planner-emitted inline-picker command must be queued for the next prompt
    (exclusive stdin) instead of dispatched inline against the live prompt."""
    monkeypatch.setattr(slash_tool, "repl_tty_interactive", lambda: True)
    dispatched = _record_dispatch(monkeypatch)

    ctx, buf, session = _ctx()
    handled = slash_tool.execute_slash_tool({"command": command, "args": args}, ctx)

    assert handled is True
    assert dispatched == []  # not run inline against the live prompt
    assert session.pending_prompt_default == expected
    assert session.pending_prompt_autosubmit is True
    assert "Launching" in buf.getvalue()


def test_interactive_picker_runs_inline_when_not_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an interactive TTY there is no live prompt to race; run inline."""
    monkeypatch.setattr(slash_tool, "repl_tty_interactive", lambda: False)
    dispatched = _record_dispatch(monkeypatch)

    ctx, _buf, session = _ctx()
    slash_tool.execute_slash_tool({"command": "/integrations", "args": ["remove", "github"]}, ctx)

    assert dispatched == ["/integrations remove github"]
    assert session.pending_prompt_default is None
    assert session.pending_prompt_autosubmit is False


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("/integrations", ["list"]),
        ("/integrations", ["show", "github"]),
        ("/health", []),
    ],
)
def test_non_picker_slash_commands_run_inline_even_in_a_tty(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    args: list[str],
) -> None:
    """Read-only / table commands do not read raw stdin, so they still dispatch
    inline and must not be deferred."""
    monkeypatch.setattr(slash_tool, "repl_tty_interactive", lambda: True)
    dispatched = _record_dispatch(monkeypatch)

    ctx, _buf, session = _ctx()
    slash_tool.execute_slash_tool({"command": command, "args": args}, ctx)

    expected = " ".join([command, *args]) if args else command
    assert dispatched == [expected]
    assert session.pending_prompt_default is None
    assert session.pending_prompt_autosubmit is False
