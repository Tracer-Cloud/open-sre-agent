"""Auto (Med) status line above the prompt input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.constants.repl_autonomy import (
    DEFAULT_AUTO_LEVEL,
    AutoLevel,
    format_auto_status_plain,
)
from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.input_prompt.layout import clip_prompt_text, prompt_line_width
from surfaces.shared.terminal.tables.provider import detect_provider_model

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session


def auto_status_ansi(session: Session, *, quiet: bool = False) -> str:
    """One row: ``Auto (Med) · allow reversible commands`` plus the model name.

    ``quiet`` is for a busy turn: Thinking / Invoking already owns the gold
    accent, so Auto recedes to DIM and does not compete.
    """
    level = getattr(session.terminal, "auto_level", DEFAULT_AUTO_LEVEL)
    left = format_auto_status_plain(level)
    # High is the default — the caption is noise on every idle frame.
    # Busy already has Thinking / Invoking; keep Auto to a short title.
    if quiet or level == AutoLevel.HIGH:
        title_end = left.find(" · ")
        if title_end >= 0:
            left = left[:title_end]
    _provider, model = detect_provider_model()
    width = prompt_line_width()
    # Model ids come from config/env. CPR stripping on the composed prompt
    # region does not remove OSC/CSI/BEL, so sanitize before interpolating.
    right = strip_terminal_controls(model or "").strip()
    gap = 2
    if right and len(left) + gap + len(right) > width:
        right = ""
    title_end = left.find(" · ")
    title = left if title_end < 0 else left[:title_end]
    rest = "" if title_end < 0 else left[title_end:]
    title_ansi = ui_theme.DIM_ANSI if quiet else ui_theme.BOLD_REPLY_MARKER_ANSI
    model_ansi = ui_theme.DIM_ANSI if quiet else ui_theme.SECONDARY_ANSI
    if not right:
        clipped = clip_prompt_text(left, width)
        pad = max(0, width - len(clipped))
        return f"{title_ansi}{clipped}{ui_theme.ANSI_RESET}{' ' * pad}"
    pad = max(width - len(left) - len(right), gap)
    return (
        f"{title_ansi}{title}{ui_theme.ANSI_RESET}"
        f"{ui_theme.DIM_ANSI}{rest}{ui_theme.ANSI_RESET}"
        f"{' ' * pad}{model_ansi}{right}{ui_theme.ANSI_RESET}"
    )


__all__ = ["auto_status_ansi"]
