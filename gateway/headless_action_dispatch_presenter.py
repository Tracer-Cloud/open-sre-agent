"""Headless action-dispatch presenter for gateway and other non-TTY agent surfaces.

Delegates to the same interactive-shell command registry the REPL uses, so
gateway turns keep running real slash commands (``/status``, ``/investigate``,
``/model set ...``); only the confirmation UX and picker hand-off are
short-circuited for a surface with no synchronous stdin to prompt against.
"""

from __future__ import annotations

from typing import Any

from tools.interactive_shell.shared import ExecutionPolicyResult


class HeadlessActionDispatchPresenter:
    """Minimal :class:`~tools.interactive_shell.dispatch.ActionDispatchPresenter` for gateway turns."""

    def __init__(self, session: Any, console: Any) -> None:
        self._session = session
        self._console = console

    def execution_allowed(
        self,
        policy: ExecutionPolicyResult,
        *,
        action_summary: str,
    ) -> bool:
        _ = (policy, action_summary)
        return True

    def slash_command_known(self, name: str) -> bool:
        from surfaces.interactive_shell.command_registry import SLASH_COMMANDS

        return name in SLASH_COMMANDS

    def dispatch_slash(self, command: str, *, policy_precleared: bool = False) -> bool:
        from surfaces.interactive_shell.command_registry import dispatch_slash

        return dispatch_slash(
            command,
            self._session,
            self._console,
            confirm_fn=None,
            is_tty=False,
            policy_precleared=policy_precleared,
        )

    def repl_tty_interactive(self) -> bool:
        return False

    def queue_slash_for_exclusive_dispatch(self, command: str) -> None:
        _ = command

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


def headless_action_dispatch_presenter_factory(
    session: Any,
    console: Any,
    confirm_fn: Any,
    is_tty: bool | None,
    action_already_listed: bool,
) -> HeadlessActionDispatchPresenter:
    _ = (confirm_fn, is_tty, action_already_listed)
    return HeadlessActionDispatchPresenter(session, console)


__all__ = ["HeadlessActionDispatchPresenter", "headless_action_dispatch_presenter_factory"]
