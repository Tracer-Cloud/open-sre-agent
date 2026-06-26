"""Turn-dispatch logic for the interactive shell UI runtime."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable

from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from rich.console import Console

from cli.interactive_shell.prompting import prompt_surface as _prompt_surface
from cli.interactive_shell.routing import router as _router
from cli.interactive_shell.routing.handle_message_with_agent.command_dispatch import (
    deterministic_command_text,
)
from cli.interactive_shell.runtime.execution import execute_routed_turn
from cli.interactive_shell.runtime.session import ReplSession
from cli.interactive_shell.runtime.state import PROMPT_REFRESH_INTERVAL_S, ReplState
from cli.interactive_shell.ui import render_banner
from cli.interactive_shell.ui.choice_menu import repl_tty_interactive

render_submitted_prompt = _prompt_surface.render_submitted_prompt

_INTERVENTION_CORRECTION_RE = re.compile(
    r"("
    r"no(?=[,.!?]|$)"
    r"|nope\b"
    r"|nvm\b"
    r"|nevermind\b|never\s*mind\b"
    r"|wrong\b"
    r"|wait(?=[,.!?]|$)"
    r"|stop(?=[,.!?]|$)"
    r"|actually\b"
    r"|scratch\s+that\b"
    r"|instead(?=[,.!?]|$)"
    r"|(?:let'?s\s+)?do\s+[^.\n]{1,60}\s+instead\b"
    r"|try\s+[^.\n]{1,60}\s+instead\b"
    r")",
    re.IGNORECASE,
)
_CONFIRMATION_TOKENS: frozenset[str] = frozenset({"", "y", "yes", "n", "no"})
_CANCEL_REQUEST_TOKENS: frozenset[str] = frozenset({"/cancel", "/stop", "/abort"})
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
        # Table-outputting commands: must complete before the next prompt_async()
        # starts, otherwise patch_stdout redraws trigger ESC[6n DSR queries whose
        # CPR responses land as literal keystrokes in the incoming prompt buffer.
        "/doctor",
        "/version",
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
    }
)
_WAIT_FOR_COMPLETION_COMMANDS: frozenset[str] = frozenset(
    {"/exit", "/quit", "/update", "/onboard", "/config"}
)


class DispatchCancelled(Exception):
    """Raised when in-flight dispatch is cancelled during confirmation."""


def looks_like_confirmation_answer(text: str | None) -> bool:
    return (text or "").strip().lower() in _CONFIRMATION_TOKENS


def looks_like_cancel_request(text: str | None) -> bool:
    return (text or "").strip().lower() in _CANCEL_REQUEST_TOKENS


def looks_like_correction(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped or stripped.startswith("```"):
        return False
    return _INTERVENTION_CORRECTION_RE.match(stripped[:80]) is not None


def dispatch_should_show_spinner(text: str, _session: ReplSession) -> bool:
    return deterministic_command_text(text.strip()) is None


def dispatch_needs_exclusive_stdin(text: str, _session: ReplSession) -> bool:
    if not repl_tty_interactive():
        return False

    t = text.strip()
    if not t:
        return False

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


def dispatch_one_turn(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    on_exit: Callable[[], None],
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> None:
    decision = _router.route_input(text, session)
    execute_routed_turn(
        text,
        session,
        console,
        on_exit=on_exit,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        decision=decision,
    )


def run_initial_input(
    initial_input: str,
    session: ReplSession,
) -> int:
    console = Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    render_banner(console)
    exit_requested = [False]

    def _early_exit() -> None:
        exit_requested[0] = True

    for line in initial_input.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        render_submitted_prompt(console, session, stripped)
        dispatch_one_turn(stripped, session, console, on_exit=_early_exit, is_tty=False)
        if exit_requested[0]:
            return 0
    return 0


def route_confirm_through_prompt(state: ReplState, prompt_text: str) -> str:
    response_event = threading.Event()
    state.begin_confirmation(response_event, prompt_text)
    try:
        while not response_event.is_set():
            cancel = state.current_cancel_event
            if cancel is not None and cancel.is_set():
                raise DispatchCancelled("cancelled while awaiting confirmation")
            response_event.wait(timeout=PROMPT_REFRESH_INTERVAL_S)
        if not state.confirm_response:
            raise DispatchCancelled("cancelled while awaiting confirmation")
        return state.confirm_response[0]
    finally:
        state.clear_confirmation()


def build_cancel_key_bindings(state: ReplState) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _on_escape(event: KeyPressEvent) -> None:
        if state.is_dispatch_running():
            state.cancel_current_dispatch()
            return
        if event.current_buffer.text:
            event.current_buffer.reset()

    @kb.add("c-l")
    def _on_ctrl_l(event: KeyPressEvent) -> None:
        event.app.renderer.clear()

    return kb


def install_session_key_bindings(pt_session: object, extra_kb: KeyBindings) -> None:
    existing = getattr(pt_session, "key_bindings", None)
    merged = merge_key_bindings([existing, extra_kb]) if existing is not None else extra_kb
    pt_session.key_bindings = merged  # type: ignore[attr-defined]


__all__ = [
    "DispatchCancelled",
    "build_cancel_key_bindings",
    "dispatch_needs_exclusive_stdin",
    "dispatch_one_turn",
    "dispatch_should_show_spinner",
    "install_session_key_bindings",
    "looks_like_cancel_request",
    "looks_like_confirmation_answer",
    "looks_like_correction",
    "route_confirm_through_prompt",
    "run_initial_input",
]
