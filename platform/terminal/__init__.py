"""Shared terminal presentation primitives (theme, prompts, error rendering)."""

from platform.terminal.errors import render_error
from platform.terminal.prompt_support import (
    CTRL_C_DOUBLE_PRESS_WINDOW_S,
    handle_ctrl_c_press,
    install_questionary_ctrl_c_double_exit,
    install_questionary_escape_cancel,
    repl_prompt_note_ctrl_c,
    repl_reset_ctrl_c_gate,
)

__all__ = [
    "CTRL_C_DOUBLE_PRESS_WINDOW_S",
    "handle_ctrl_c_press",
    "install_questionary_ctrl_c_double_exit",
    "install_questionary_escape_cancel",
    "render_error",
    "repl_prompt_note_ctrl_c",
    "repl_reset_ctrl_c_gate",
]
