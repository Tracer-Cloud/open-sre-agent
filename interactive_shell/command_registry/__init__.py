"""Composable slash-command registry for the interactive REPL."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from itertools import chain

from rich.console import Console
from rich.markup import escape

from interactive_shell.command_registry.agents import COMMANDS as AGENTS_COMMANDS
from interactive_shell.command_registry.alerts import COMMANDS as ALERTS_COMMANDS
from interactive_shell.command_registry.background_cmds import (
    COMMANDS as BACKGROUND_COMMANDS,
)
from interactive_shell.command_registry.cli_parity import (
    COMMANDS as PARITY_COMMANDS,
)
from interactive_shell.command_registry.diagnostics_cmds import (
    COMMANDS as DIAGNOSTICS_COMMANDS,
)
from interactive_shell.command_registry.help import COMMANDS as HELP_COMMANDS
from interactive_shell.command_registry.integrations import (
    COMMANDS as INTEGRATIONS_COMMANDS,
)
from interactive_shell.command_registry.investigation import (
    COMMANDS as INVESTIGATION_COMMANDS,
)
from interactive_shell.command_registry.model import COMMANDS as MODEL_COMMANDS
from interactive_shell.command_registry.model import (
    switch_llm_provider,
    switch_reasoning_model,
    switch_toolcall_model,
)
from interactive_shell.command_registry.privacy_cmds import COMMANDS as PRIVACY_COMMANDS
from interactive_shell.command_registry.rca_cmds import COMMANDS as RCA_COMMANDS
from interactive_shell.command_registry.repl_data import (
    load_llm_settings,
    load_verified_integrations,
)
from interactive_shell.command_registry.session_cmds import COMMANDS as SESSION_COMMANDS
from interactive_shell.command_registry.settings_cmds import (
    COMMANDS as SETTINGS_COMMANDS,
)
from interactive_shell.command_registry.suggestions import closest_choice
from interactive_shell.command_registry.system import COMMANDS as SYSTEM_COMMANDS
from interactive_shell.command_registry.tasks_cmds import COMMANDS as TASK_COMMANDS
from interactive_shell.command_registry.theme import COMMANDS as THEME_COMMANDS
from interactive_shell.command_registry.tools_cmds import COMMANDS as TOOLS_COMMANDS
from interactive_shell.command_registry.types import SlashCommand
from interactive_shell.command_registry.watch_cmds import COMMANDS as WATCH_COMMANDS
from interactive_shell.runtime import ReplSession
from interactive_shell.ui import ERROR
from interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.shared import allow_tool

_MERGED_SEQUENCE = tuple(
    chain(
        HELP_COMMANDS,
        SESSION_COMMANDS,
        THEME_COMMANDS,
        BACKGROUND_COMMANDS,
        SETTINGS_COMMANDS,
        DIAGNOSTICS_COMMANDS,
        INTEGRATIONS_COMMANDS,
        MODEL_COMMANDS,
        TOOLS_COMMANDS,
        INVESTIGATION_COMMANDS,
        RCA_COMMANDS,
        TASK_COMMANDS,
        WATCH_COMMANDS,
        PRIVACY_COMMANDS,
        AGENTS_COMMANDS,
        ALERTS_COMMANDS,
        PARITY_COMMANDS,
        SYSTEM_COMMANDS,
    )
)

SLASH_COMMANDS: dict[str, SlashCommand] = {cmd.name: cmd for cmd in _MERGED_SEQUENCE}

# Slash commands that adopt a different session file must record the turn after
# the handler settles session identity (see /resume).
_DEFER_SLASH_RECORDING: frozenset[str] = frozenset({"/resume"})


def dispatch_slash(
    command_line: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    policy_precleared: bool = False,
) -> bool:
    """Dispatch a slash command line. Returns False iff the REPL should exit.

    When ``policy_precleared`` is True, skip the execution gate (caller already ran
    :func:`execution_allowed`) and run the handler directly. Only valid for lines
    the registry resolves to a known command, or bare ``/`` after an equivalent
    gate for help.
    """
    env_backup = os.environ.get("OPENSRE_INTERACTIVE")
    if is_tty is False:
        os.environ["OPENSRE_INTERACTIVE"] = "0"

    try:
        stripped = command_line.strip()
        if stripped == "/":
            from interactive_shell.command_registry.help import _cmd_help

            if policy_precleared:
                session.record("slash", stripped, ok=True)
                return _cmd_help(session, console, [])

            gate = allow_tool("slash")
            if not execution_allowed(
                gate,
                session=session,
                console=console,
                action_summary=stripped,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            ):
                session.record("slash", stripped, ok=False)
                return True
            session.record("slash", stripped, ok=True)
            return _cmd_help(session, console, [])

        parts = stripped.split()
        if not parts:
            return True
        name = parts[0].lower()
        if name in ("/watch", "/unwatch"):
            head = parts[0]
            body = stripped[len(head) :].strip()
            try:
                # Use POSIX mode on all platforms so quoted values are unwrapped
                # consistently (e.g., --max-cpu "80" -> 80).
                args = shlex.split(body, posix=True)
            except ValueError:
                args = body.split()
        else:
            args = parts[1:]
        cmd = SLASH_COMMANDS.get(name)
        if cmd is None:
            suggestion = closest_choice(name, tuple(SLASH_COMMANDS))
            session.record("slash", stripped, ok=False)
            console.print()
            if suggestion is None:
                console.print(
                    f"[{ERROR}]unknown command:[/] {escape(name)}  (type [bold]/help[/bold])"
                )
            else:
                console.print(
                    f"[{ERROR}]unknown command:[/] {escape(name)}  "
                    f"Did you mean [bold]{escape(suggestion)}[/bold]? "
                    "(type [bold]/help[/bold])"
                )
            return True
        if cmd.validate_args is not None:
            validation_error = cmd.validate_args(args)
            if validation_error is not None:
                console.print(validation_error)
                session.record("slash", stripped, ok=False)
                return True
        if policy_precleared:
            if name not in _DEFER_SLASH_RECORDING:
                session.record("slash", stripped, ok=True)
            return cmd.handler(session, console, args)
        policy = allow_tool("slash")
        if not execution_allowed(
            policy,
            session=session,
            console=console,
            action_summary=stripped,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
        ):
            session.record("slash", stripped, ok=False)
            return True
        if name not in _DEFER_SLASH_RECORDING:
            session.record("slash", stripped, ok=True)
        return cmd.handler(session, console, args)
    finally:
        if is_tty is False:
            if env_backup is None:
                del os.environ["OPENSRE_INTERACTIVE"]
            else:
                os.environ["OPENSRE_INTERACTIVE"] = env_backup


__all__ = [
    "SLASH_COMMANDS",
    "SlashCommand",
    "dispatch_slash",
    "load_llm_settings",
    "load_verified_integrations",
    "switch_llm_provider",
    "switch_reasoning_model",
    "switch_toolcall_model",
]
