"""Unit tests for modular slash-command registry."""

from __future__ import annotations

import io

from rich.console import Console

from cli.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from cli.interactive_shell.command_registry.integrations import (
    _INTEGRATIONS_FIRST_ARGS,
    _MCP_FIRST_ARGS,
)
from cli.interactive_shell.command_registry.investigation import (
    _INVESTIGATE_FIRST_ARGS,
    _TEMPLATE_FIRST_ARGS,
)
from cli.interactive_shell.command_registry.model.command import _MODEL_FIRST_ARGS
from cli.interactive_shell.command_registry.settings_cmds import (
    _TRUST_FIRST_ARGS,
    _VERBOSE_FIRST_ARGS,
)
from cli.interactive_shell.command_registry.tools_cmds import _TOOLS_FIRST_ARGS
from cli.interactive_shell.runtime.session import ReplSession


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_slash_registry_includes_modular_commands() -> None:
    for name in (
        "/help",
        "/?",
        "/exit",
        "/model",
        "/tools",
        "/integrations",
        "/investigate",
        "/tasks",
        "/watch",
        "/watches",
        "/unwatch",
        "/health",
    ):
        assert name in SLASH_COMMANDS


def test_dispatch_unknown_command_stays_in_repl() -> None:
    session = ReplSession()
    console, buf = _capture()
    assert dispatch_slash("/not-a-real-slash", session, console) is True
    assert "unknown command" in buf.getvalue()


def test_registry_first_arg_completion_hints_co_located_with_handlers() -> None:
    """Merged registry exposes the same first-arg tab tuples defined in each module."""
    expected: dict[str, tuple[tuple[str, str], ...]] = {
        "/model": _MODEL_FIRST_ARGS,
        "/tools": _TOOLS_FIRST_ARGS,
        "/integrations": _INTEGRATIONS_FIRST_ARGS,
        "/mcp": _MCP_FIRST_ARGS,
        "/investigate": _INVESTIGATE_FIRST_ARGS,
        "/template": _TEMPLATE_FIRST_ARGS,
        "/trust": _TRUST_FIRST_ARGS,
        "/verbose": _VERBOSE_FIRST_ARGS,
    }
    for name, tup in expected.items():
        assert SLASH_COMMANDS[name].first_arg_completions == tup

    assert SLASH_COMMANDS["/help"].first_arg_completions == ()
