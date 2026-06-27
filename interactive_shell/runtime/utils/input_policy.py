"""Prompt input and stdin coordination policy for runtime turns."""

from __future__ import annotations

from interactive_shell.harness.orchestration.command_dispatch import (
    deterministic_command_text,
)
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.ui.components.choice_menu import repl_tty_interactive

_EXCLUSIVE_STDIN_MENU_COMMANDS: frozenset[str] = frozenset(
    {
        "/history",
        "/help",
        "/integrations",
        "/investigate",
        "/mcp",
        "/model",
        "/tools",
        "/template",
        "/trust",
        "/verbose",
        "/?",
        # Table-outputting commands must complete before the next prompt_async()
        # starts, otherwise patch_stdout redraws trigger ESC[6n DSR queries whose
        # CPR responses land as literal keystrokes in the incoming prompt buffer.
        "/doctor",
        "/version",
        "/verify",
        "/status",
        "/cost",
        "/tasks",
        "/watches",
        "/alerts",
        "/privacy",
        "/context",
        "/fleet",
        "/compact",
        "/welcome",
        "/sessions",
        "/resume",
        "/new",
        "/rca",
    }
)
_EXCLUSIVE_STDIN_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("/integrations", "setup"),
        # ``remove`` drives a native inline arrow-key picker (raw os.read on
        # stdin). Without exclusive stdin the concurrent prompt_async() steals
        # keystrokes and CPR responses leak into the next prompt buffer.
        ("/integrations", "remove"),
        ("/mcp", "connect"),
        ("/mcp", "disconnect"),
        ("/rca", "history"),
        ("/rca", "list"),
        ("/rca", "ls"),
        ("/rca", "show"),
        ("/rca", "save"),
    }
)
_WAIT_FOR_COMPLETION_COMMANDS: frozenset[str] = frozenset(
    {"/exit", "/quit", "/update", "/onboard", "/config"}
)


def turn_should_show_spinner(text: str, _session: ReplSession) -> bool:
    # This deterministic command check is UI-only. It must never become an
    # execution shortcut; submitted turns still go through the LLM planner before
    # any slash or shell action can run.
    return deterministic_command_text(text.strip()) is None


def turn_needs_exclusive_stdin(text: str, _session: ReplSession) -> bool:
    if not repl_tty_interactive():
        return False

    t = text.strip()
    if not t:
        return False

    # Reserve stdin early for literal command-shaped input, but do not dispatch
    # here. Execution remains planner-owned so there are no command fast paths.
    dispatch_text = deterministic_command_text(t)
    if dispatch_text is None:
        return False

    parts = dispatch_text.split()
    if not parts:
        return False
    name = parts[0].lower()
    args = [arg.lower() for arg in parts[1:]]

    if name in _WAIT_FOR_COMPLETION_COMMANDS:
        return True
    if name == "/theme":
        return True
    if name in _EXCLUSIVE_STDIN_MENU_COMMANDS and not args:
        return True
    if name == "/tests" and not args:
        return True
    return bool(args and (name, args[0]) in _EXCLUSIVE_STDIN_SUBCOMMANDS)


__all__ = [
    "turn_needs_exclusive_stdin",
    "turn_should_show_spinner",
]
