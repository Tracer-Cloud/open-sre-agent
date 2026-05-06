"""Unit tests for modular slash-command registry."""

from __future__ import annotations

import io

from rich.console import Console

from app.cli.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.commands import SLASH_COMMANDS as COMMANDS_EXPORT
from app.cli.interactive_shell.session import ReplSession


def test_commands_shim_reexports_same_registry() -> None:
    assert COMMANDS_EXPORT is SLASH_COMMANDS


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_slash_registry_includes_modular_commands() -> None:
    for name in (
        "/help",
        "/?",
        "/exit",
        "/model",
        "/list",
        "/integrations",
        "/investigate",
        "/tasks",
        "/health",
    ):
        assert name in SLASH_COMMANDS


def test_dispatch_unknown_command_stays_in_repl() -> None:
    session = ReplSession()
    console, buf = _capture()
    assert dispatch_slash("/not-a-real-slash", session, console) is True
    assert "unknown command" in buf.getvalue()
