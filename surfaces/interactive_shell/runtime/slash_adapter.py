"""REPL adapter for slash-command action tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from rich.console import Console

from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.command_registry.slash_catalog import (
    slash_invoke_input_schema,
    slash_invoke_tool_description,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import BOLD_BRAND, DIM, repl_tty_interactive
from surfaces.interactive_shell.ui.execution_confirm import execution_allowed
from surfaces.interactive_shell.utils.telemetry.turn_outcome import (
    format_terminal_turn_outcome,
)
from tools.interactive_shell.shared.execution_policy import ExecutionPolicyResult


class SlashPorts(Protocol):
    def command_exists(self, name: str) -> bool:
        raise NotImplementedError

    def tool_description(self) -> str:
        raise NotImplementedError

    def input_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def tty_interactive(self) -> bool:
        raise NotImplementedError

    def launching_message(self, command: str) -> str:
        raise NotImplementedError

    def format_turn_outcome(
        self,
        command: str,
        *,
        ok: bool,
    ) -> str:
        raise NotImplementedError

    def execution_allowed(
        self,
        *,
        policy: ExecutionPolicyResult,
        session: Session,
        console: Console,
        action_summary: str,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
        action_already_listed: bool,
    ) -> bool:
        raise NotImplementedError

    def dispatch(
        self,
        command: str,
        *,
        session: Session,
        console: Console,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
        policy_precleared: bool = False,
    ) -> bool:
        raise NotImplementedError


class ReplSlashPorts:
    def command_exists(self, name: str) -> bool:
        return name in SLASH_COMMANDS

    def tool_description(self) -> str:
        return slash_invoke_tool_description()

    def input_schema(self) -> dict[str, Any]:
        return slash_invoke_input_schema()

    def tty_interactive(self) -> bool:
        return repl_tty_interactive()

    def launching_message(self, command: str) -> str:
        return f"[{DIM}]Launching[/] [{BOLD_BRAND}]{command}[/]…"

    def format_turn_outcome(
        self,
        command: str,
        *,
        ok: bool,
    ) -> str:
        return format_terminal_turn_outcome(
            command,
            kind="slash",
            ok=ok,
        )

    def execution_allowed(
        self,
        *,
        policy: ExecutionPolicyResult,
        session: Session,
        console: Console,
        action_summary: str,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
        action_already_listed: bool,
    ) -> bool:
        return execution_allowed(
            policy,
            session=session,
            console=console,
            action_summary=action_summary,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            action_already_listed=action_already_listed,
        )

    def dispatch(
        self,
        command: str,
        *,
        session: Session,
        console: Console,
        confirm_fn: Callable[[str], str] | None,
        is_tty: bool | None,
        policy_precleared: bool = False,
    ) -> bool:
        return dispatch_slash(
            command,
            session,
            console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            policy_precleared=policy_precleared,
        )


class HeadlessSlashPorts(ReplSlashPorts):
    """Slash ports for non-interactive headless and gateway turns."""

    def tty_interactive(self) -> bool:
        return False

    def launching_message(self, command: str) -> str:
        return f"Launching {command}…"

    def format_turn_outcome(
        self,
        command: str,
        *,
        ok: bool,
    ) -> str:
        status = "succeeded" if ok else "failed"
        return f"Slash command {command} {status}."


def repl_slash_ports() -> SlashPorts:
    return ReplSlashPorts()


def headless_slash_ports() -> SlashPorts:
    return HeadlessSlashPorts()


__all__ = [
    "HeadlessSlashPorts",
    "ReplSlashPorts",
    "SlashPorts",
    "headless_slash_ports",
    "repl_slash_ports",
]
