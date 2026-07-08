"""prompt-toolkit style construction and live theme refresh."""

from __future__ import annotations

from contextlib import suppress

from prompt_toolkit.styles import Style

from platform.terminal import theme as ui_theme
from surfaces.interactive_shell.runtime import Session


def _build_prompt_style() -> Style:
    theme = ui_theme.get_active_theme()
    text_fg = f"fg:{theme.TEXT}"
    return Style.from_dict(
        {
            "prompt-frame-line": f"bold {theme.HIGHLIGHT}",
            "": text_fg,
            "default": text_fg,
            "repl-slash-command": f"bold {theme.HIGHLIGHT} bg:{theme.BG}",
            "completion-menu": f"bg:{theme.BG}",
            "completion-menu.completion": f"{theme.TEXT} bg:{theme.BG}",
            "completion-menu.completion.current": f"bold {theme.HIGHLIGHT} bg:{theme.BG}",
            "completion-menu.meta.completion": f"{theme.DIM} bg:{theme.BG}",
            "completion-menu.meta.completion.current": f"{theme.HIGHLIGHT} bg:{theme.BG}",
            "completion-menu.border": theme.DIM,
            "scrollbar.background": f"bg:{theme.BG}",
            "scrollbar.button": f"bg:{theme.DIM}",
            # prompt_toolkit defaults the ``bottom-toolbar`` style to
            # ``reverse:noinherit``, which paints the toolbar as a dark
            # highlighted band across the terminal. Clear the reverse
            # so the spinner + hint sit on the regular terminal bg
            # (Claude Code-style flat layout).
            "bottom-toolbar": "noreverse",
            "bottom-toolbar.text": "noreverse",
        }
    )


def refresh_prompt_theme(session: Session) -> None:
    """Apply the active palette to the running prompt (input text + placeholder)."""
    app = session.terminal.prompt_app
    if app is None:
        return
    app.style = _build_prompt_style()
    # Between prompt_async turns the Application is not running; invalidate() then
    # triggers ESC[6n CPR queries whose responses leak as literal text on the
    # next idle-hint line (e.g. ``^[[1;1R/ for commands``).
    if not app.is_running:
        return
    if app.renderer is not None:
        with suppress(Exception):
            app.renderer.clear()
    app.invalidate()
