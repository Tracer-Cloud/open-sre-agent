"""Composable slash-command registry for the interactive REPL."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from itertools import chain
from typing import Any

from rich.console import Console

from core.agent_harness.session.terminal_access import pop_turn_outcome_hint, session_terminal
from surfaces.interactive_shell.command_registry.agents import COMMANDS as AGENTS_COMMANDS
from surfaces.interactive_shell.command_registry.alerts import COMMANDS as ALERTS_COMMANDS
from surfaces.interactive_shell.command_registry.background_cmds import (
    COMMANDS as BACKGROUND_COMMANDS,
)
from surfaces.interactive_shell.command_registry.cli_parity import (
    COMMANDS as PARITY_COMMANDS,
)
from surfaces.interactive_shell.command_registry.diagnostics_cmds import (
    COMMANDS as DIAGNOSTICS_COMMANDS,
)
from surfaces.interactive_shell.command_registry.help import COMMANDS as HELP_COMMANDS
from surfaces.interactive_shell.command_registry.integrations import (
    COMMANDS as INTEGRATIONS_COMMANDS,
)
from surfaces.interactive_shell.command_registry.investigation import (
    COMMANDS as INVESTIGATION_COMMANDS,
)
from surfaces.interactive_shell.command_registry.model import COMMANDS as MODEL_COMMANDS
from surfaces.interactive_shell.command_registry.model import (
    switch_llm_provider,
    switch_reasoning_model,
    switch_toolcall_model,
)
from surfaces.interactive_shell.command_registry.privacy_cmds import (
    COMMANDS as PRIVACY_COMMANDS,
)
from surfaces.interactive_shell.command_registry.rca_cmds import COMMANDS as RCA_COMMANDS
from surfaces.interactive_shell.command_registry.repl_data import (
    load_llm_settings,
    load_verified_integrations,
)
from surfaces.interactive_shell.command_registry.session_cmds import (
    COMMANDS as SESSION_COMMANDS,
)
from surfaces.interactive_shell.command_registry.settings_cmds import (
    COMMANDS as SETTINGS_COMMANDS,
)
from surfaces.interactive_shell.command_registry.suggestions import (
    format_unknown_slash_message,
    resolve_literal_slash_typo,
)
from surfaces.interactive_shell.command_registry.system import COMMANDS as SYSTEM_COMMANDS
from surfaces.interactive_shell.command_registry.tasks_cmds import COMMANDS as TASK_COMMANDS
from surfaces.interactive_shell.command_registry.theme import COMMANDS as THEME_COMMANDS
from surfaces.interactive_shell.command_registry.tools_cmds import COMMANDS as TOOLS_COMMANDS
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.command_registry.watch_cmds import COMMANDS as WATCH_COMMANDS
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from surfaces.interactive_shell.utils.telemetry.console_capture import capture_console_segment
from surfaces.interactive_shell.utils.telemetry.turn_outcome import format_terminal_turn_outcome
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


def _latest_record_ok(session: Session, kind: str, *, default: bool = True) -> bool:
    """Return ``ok`` from the newest history row of ``kind`` after the handler runs."""
    for entry in reversed(session.history):
        if entry.get("type") == kind:
            return bool(entry.get("ok", default))
    return default


def _latest_slash_record(session: Session) -> dict[str, Any] | None:
    for entry in reversed(session.history):
        if entry.get("type") == "slash":
            return entry
    return None


def _attach_slash_analytics(
    session: Session,
    command_line: str,
    *,
    captured_output: str,
) -> None:
    latest = _latest_slash_record(session)
    ok = _latest_record_ok(session, "slash")
    if latest is not None and latest.get("slash_outcome"):
        response_text = str(latest.get("response_text") or "").strip()
    else:
        response_text = format_terminal_turn_outcome(
            command_line,
            kind="slash",
            ok=ok,
            captured_output=captured_output,
            outcome_hint=pop_turn_outcome_hint(session),
            include_captured_on_summary_only=session_terminal(session) is None,
        )
    session.complete_latest_record(
        "slash",
        response_text=response_text,
    )


def dispatch_slash(
    command_line: str,
    session: Session,
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

    stripped = command_line.strip()
    slash_recorded = False

    def record_slash(
        *,
        ok: bool = True,
        response_text: str | None = None,
        slash_outcome: str | None = None,
    ) -> None:
        nonlocal slash_recorded
        session.record(
            "slash",
            stripped,
            ok=ok,
            response_text=response_text,
            slash_outcome=slash_outcome,
        )
        slash_recorded = True

    try:
        with capture_console_segment(console) as get_captured:
            try:
                if stripped == "/":
                    from surfaces.interactive_shell.command_registry.help import _cmd_help

                    if policy_precleared:
                        record_slash(ok=True)
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
                        record_slash(ok=False)
                        return True
                    record_slash(ok=True)
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
                    typo_message = format_unknown_slash_message(
                        stripped,
                        command_names=tuple(SLASH_COMMANDS),
                    )
                    record_slash(
                        ok=False,
                        response_text=typo_message,
                        slash_outcome="unknown_command",
                    )
                    console.print()
                    console.print(typo_message)
                    return True
                typo = resolve_literal_slash_typo(stripped, SLASH_COMMANDS)
                if typo is not None:
                    record_slash(
                        ok=False,
                        response_text=typo.message,
                        slash_outcome=typo.outcome,
                    )
                    console.print()
                    console.print(typo.message)
                    return True
                if cmd.validate_args is not None:
                    validation_error = cmd.validate_args(args)
                    if validation_error is not None:
                        record_slash(ok=False)
                        console.print(validation_error)
                        return True
                if policy_precleared:
                    if name not in _DEFER_SLASH_RECORDING:
                        record_slash(ok=True)
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
                    record_slash(ok=False)
                    return True
                if name not in _DEFER_SLASH_RECORDING:
                    record_slash(ok=True)
                return cmd.handler(session, console, args)
            finally:
                if slash_recorded:
                    _attach_slash_analytics(
                        session,
                        stripped,
                        captured_output=get_captured(),
                    )
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
