"""Shared color theme for the interactive shell."""

from __future__ import annotations

type RGBColor = tuple[int, int, int]

OPENCLAW_CORAL_RGB: RGBColor = (255, 95, 86)
OPENCLAW_ORANGE_RGB: RGBColor = (255, 122, 69)
OPENCLAW_AMBER_RGB: RGBColor = (255, 190, 104)


def _hex(rgb: RGBColor) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


OPENCLAW_CORAL = _hex(OPENCLAW_CORAL_RGB)
OPENCLAW_ORANGE = _hex(OPENCLAW_ORANGE_RGB)
OPENCLAW_AMBER = _hex(OPENCLAW_AMBER_RGB)

BANNER_PRIMARY = OPENCLAW_CORAL
BANNER_SECONDARY = OPENCLAW_ORANGE
BANNER_TERTIARY = OPENCLAW_AMBER
PROMPT_ACCENT_RGB = OPENCLAW_ORANGE_RGB
TERMINAL_ACCENT = OPENCLAW_ORANGE
TERMINAL_ACCENT_BOLD = f"bold {OPENCLAW_ORANGE}"

# Muted rose — less glare than Rich ``red`` on dark REPL backgrounds.
TERMINAL_ERROR = "#c99a9a"

PROMPT_ACCENT_ANSI = (
    f"\x1b[1;38;2;{PROMPT_ACCENT_RGB[0]};{PROMPT_ACCENT_RGB[1]};{PROMPT_ACCENT_RGB[2]}m"
)
ANSI_RESET = "\x1b[0m"

# Layout chrome — minimal separators and secondary labels (Claude Code–style restraint).
SEPARATOR_COLOR = "#2e2a27"
DIM_TEXT_COLOR = "#6b6561"
BANNER_UI_DIVIDER = "#3d3833"
# Truecolour ANSI for bracketed turn counter in the prompt (same RGB as DIM_TEXT_COLOR).
DIM_COUNTER_ANSI = "\x1b[38;2;107;101;97m"
