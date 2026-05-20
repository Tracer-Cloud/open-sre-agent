"""Interactive shell terminal runtime package."""

from __future__ import annotations

from app.agents.sweep import run_startup_sweep
from app.analytics.provider import get_analytics
from app.cli.interactive_shell import commands as _commands
from app.cli.interactive_shell.prompting import prompt_surface as _prompt_surface
from app.cli.interactive_shell.routing import router as _router
from app.cli.interactive_shell.runtime import HotReloadCoordinator, ReplSession
from app.cli.interactive_shell.ui import render_banner
from app.cli.interactive_shell.ui.choice_menu import repl_tty_interactive
from app.cli.interactive_shell.ui.streaming import _CHARS_PER_TOKEN

from .dispatch import (
    DispatchCancelled,
    _build_cancel_key_bindings,
    _dispatch_needs_exclusive_stdin_impl,
    _dispatch_one_turn,
    _dispatch_should_show_spinner,
    _install_session_key_bindings,
    _looks_like_cancel_request,
    _looks_like_confirmation_answer,
    _looks_like_correction,
    _route_confirm_through_prompt,
    _run_initial_input,
)
from .entrypoint import _repl_main, run_repl
from .execution import (
    dispatch_slash,
)
from .execution import (
    run_new_alert as _run_new_alert,
)
from .state import _PROMPT_REFRESH_INTERVAL_S
from .state import ReplState as _ReplState
from .state import SpinnerState as _SpinnerState
from .terminal_runtime import _run_interactive, _StreamingConsole

resolve_cli_command = _router.resolve_cli_command
route_input = _router.route_input


def _dispatch_needs_exclusive_stdin(text: str, session: ReplSession) -> bool:
    return _dispatch_needs_exclusive_stdin_impl(
        text,
        session,
        tty_interactive_fn=repl_tty_interactive,
    )


__all__ = [
    "DispatchCancelled",
    "_CHARS_PER_TOKEN",
    "_PROMPT_REFRESH_INTERVAL_S",
    "_ReplState",
    "_SpinnerState",
    "_StreamingConsole",
    "_build_cancel_key_bindings",
    "_dispatch_needs_exclusive_stdin",
    "_dispatch_one_turn",
    "_dispatch_should_show_spinner",
    "_install_session_key_bindings",
    "_looks_like_cancel_request",
    "_looks_like_confirmation_answer",
    "_looks_like_correction",
    "_run_new_alert",
    "_repl_main",
    "_route_confirm_through_prompt",
    "_run_initial_input",
    "_run_interactive",
    "_prompt_surface",
    "_router",
    "_commands",
    "dispatch_slash",
    "get_analytics",
    "render_banner",
    "run_startup_sweep",
    "HotReloadCoordinator",
    "repl_tty_interactive",
    "resolve_cli_command",
    "route_input",
    "run_repl",
]
