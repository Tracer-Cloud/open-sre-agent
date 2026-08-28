"""Prompt-toolkit key bindings for the REPL prompt."""

from __future__ import annotations

from typing import Protocol

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.filters import has_completions
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent

from infrastructure.terminal.prompt_support import (
    CTRL_C_DOUBLE_PRESS_WINDOW_S,
    repl_prompt_ctrl_c_should_exit,
)


class _DispatchCancelState(Protocol):
    def is_dispatch_running(self) -> bool:
        raise NotImplementedError

    def cancel_current_dispatch(self) -> None:
        raise NotImplementedError

    def request_exit(self) -> None:
        """Request an orderly exit from the interactive shell."""

    def arm_ctrl_c_exit_hint(self, duration_seconds: float) -> None:
        """Show the transient double-press exit hint."""

    def clear_ctrl_c_exit_hint(self) -> None:
        """Clear the transient double-press exit hint."""


# Keystroke escape (xterm modifyOtherKeys for Shift+Enter), not a colour code.
_SHIFT_ENTER_SEQUENCE = "\x1b[27;2;13~"


def _tab_expand_or_menu(buffer: Buffer) -> None:
    """Apply the current completion or open the menu when several choices exist."""
    if buffer.complete_state:
        state = buffer.complete_state
        completion = state.current_completion
        if completion is None and state.completions:
            completion = state.completions[0]
        if completion is not None:
            buffer.apply_completion(completion)
        return
    if buffer.completer is None:
        return
    completions = list(
        buffer.completer.get_completions(
            buffer.document,
            CompleteEvent(completion_requested=True),
        )
    )
    if len(completions) == 1:
        buffer.apply_completion(completions[0])
    else:
        buffer.start_completion(select_first=True)


def _build_prompt_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("c-m")
    def _accept_turn(event: KeyPressEvent) -> None:
        if event.data == _SHIFT_ENTER_SEQUENCE:
            event.current_buffer.newline(copy_margin=False)
            return
        event.current_buffer.validate_and_handle()

    @bindings.add("tab")
    def _tab_complete(event: object) -> None:
        _tab_expand_or_menu(event.current_buffer)  # type: ignore[attr-defined]

    @bindings.add("s-tab")
    def _shift_tab_complete(event: object) -> None:
        buff = event.current_buffer  # type: ignore[attr-defined]
        if buff.complete_state:
            buff.complete_previous()
        else:
            buff.start_completion(select_first=False)

    @bindings.add("down", filter=has_completions)
    def _next_completion(event: object) -> None:
        event.current_buffer.complete_next()  # type: ignore[attr-defined]

    @bindings.add("up", filter=has_completions)
    def _previous_completion(event: object) -> None:
        event.current_buffer.complete_previous()  # type: ignore[attr-defined]

    return bindings


def build_cancel_key_bindings(state: _DispatchCancelState) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-c", eager=True)
    def _on_ctrl_c(event: KeyPressEvent) -> None:
        event.current_buffer.reset()
        if state.is_dispatch_running():
            state.clear_ctrl_c_exit_hint()
            state.cancel_current_dispatch()
            event.app.invalidate()
            return
        if repl_prompt_ctrl_c_should_exit():
            state.clear_ctrl_c_exit_hint()
            state.request_exit()
            event.app.exit(result="")
            return
        state.arm_ctrl_c_exit_hint(CTRL_C_DOUBLE_PRESS_WINDOW_S)
        event.app.invalidate()

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
