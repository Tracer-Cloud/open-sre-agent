"""REPL action-dispatch presenter — slash dispatch and provider-switch hooks."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import BOLD_BRAND, DIM
from tools.interactive_shell.dispatch import ActionDispatchPresenter
from tools.interactive_shell.shared import ExecutionPolicyResult


class ReplActionDispatchPresenter:
    """Surface implementation of :class:`ActionDispatchPresenter` for the interactive REPL."""

    def __init__(
        self,
        session: Session,
        console: Console,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        action_already_listed: bool = False,
    ) -> None:
        self._session = session
        self._console = console
        self._confirm_fn = confirm_fn
        self._is_tty = is_tty
        self._action_already_listed = action_already_listed

    def execution_allowed(
        self,
        policy: ExecutionPolicyResult,
        *,
        action_summary: str,
    ) -> bool:
        from surfaces.interactive_shell.ui.execution_confirm import execution_allowed

        return execution_allowed(
            policy,
            session=self._session,
            console=self._console,
            action_summary=action_summary,
            confirm_fn=self._confirm_fn,
            is_tty=self._is_tty,
            action_already_listed=self._action_already_listed,
        )

    def slash_command_known(self, name: str) -> bool:
        from surfaces.interactive_shell.command_registry import SLASH_COMMANDS

        return name in SLASH_COMMANDS

    def dispatch_slash(self, command: str, *, policy_precleared: bool = False) -> bool:
        from surfaces.interactive_shell.command_registry import dispatch_slash

        return dispatch_slash(
            command,
            self._session,
            self._console,
            confirm_fn=self._confirm_fn,
            is_tty=self._is_tty,
            policy_precleared=policy_precleared,
        )

    def repl_tty_interactive(self) -> bool:
        from surfaces.interactive_shell.ui.components.choice_menu import (
            repl_tty_interactive,
        )

        return repl_tty_interactive()

    def queue_slash_for_exclusive_dispatch(self, command: str) -> None:
        self._console.print(f"[{DIM}]Launching[/] [{BOLD_BRAND}]{escape(command)}[/]…")

    def format_turn_outcome(self, command: str, *, kind: str, ok: bool) -> str:
        from surfaces.interactive_shell.utils.telemetry.turn_outcome import (
            format_terminal_turn_outcome,
        )

        return format_terminal_turn_outcome(command, kind=kind, ok=ok)

    def switch_llm_provider(self, target: str) -> bool:
        from surfaces.interactive_shell.command_registry import switch_llm_provider

        return switch_llm_provider(target, self._console)

    def switch_reasoning_model(self, target: str) -> bool:
        from surfaces.interactive_shell.command_registry import switch_reasoning_model

        return switch_reasoning_model(target, self._console)


def make_repl_action_dispatch_presenter(
    session: Session,
    console: Console,
    confirm_fn: Callable[[str], str] | None,
    is_tty: bool | None,
    action_already_listed: bool,
) -> ActionDispatchPresenter:
    """Construct a :class:`ReplActionDispatchPresenter` for the action tool provider."""
    return ReplActionDispatchPresenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    )


__all__ = ["ReplActionDispatchPresenter", "make_repl_action_dispatch_presenter"]
