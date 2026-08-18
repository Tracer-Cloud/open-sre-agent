"""Responsive splash-screen layout with the Braille OSRE logomark.

Three layout states, selected from the terminal width at render time:

* ``wide``    (>= 90 cols)  large Braille logo beside the splash content
                            (subtitle + description)
* ``medium``  (60-89 cols)  small Braille logo beside the same condensed content
* ``narrow``  (< 60 cols)   no logo; simple stacked text-only layout

The Braille logos are fixed, pre-rendered string tuples — never generated or
resized at runtime. All width math uses Rich's ``cell_len`` so Braille glyphs
and styled text are measured by display cells, not Python string length.
"""

from __future__ import annotations

from typing import Literal

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from platform.terminal.theme import BRAND, DIM, SECONDARY

SplashMode = Literal["wide", "medium", "narrow"]

SPLASH_WIDE_MIN_WIDTH = 90
SPLASH_MEDIUM_MIN_WIDTH = 60

# Fixed Braille logomarks (OSRE "O" mark). Checked in as static art; pick a
# variant with select_splash_mode() — do not resize these at runtime.
BRAILLE_LOGO_LARGE: tuple[str, ...] = (
    "⠀⠀⣠⣶⡿⠿⢿⡿⢿⣶⣌⠙⢶⣄",
    "⢠⣾⡟⢁⣴⡿⠋⠀⠀⠈⢻⣷⡀⢻⣷⡀",
    "⣾⡟⠀⣾⣿⠁⠀⠀⠀⠀⠀⢿⣷⠀⣿⣷",
    "⣿⡇⠰⣿⣏⠀⠀⠀⠀⠀⠀⢸⣿⠀⣿⣿",
    "⢿⣷⠀⢿⣿⡀⠀⠀⠀⠀⠀⣾⡿⠀⣿⡿",
    "⠈⢿⣧⡈⠻⣷⣄⡀⠀⢀⣼⡿⠁⣼⡿⠁",
    "⠀⠀⠙⠿⢷⣶⣾⣷⡾⠿⢋⡤⠞⠋",
)

BRAILLE_LOGO_SMALL: tuple[str, ...] = (
    "⠀⣠⡶⢟⣻⠟⠻⣶⣝⢲⣄",
    "⣼⡏⣰⡿⠁⠀⠀⠈⢿⡆⢹⣧",
    "⣿⡁⣿⡇⠀⠀⠀⠀⢸⣿⢸⣿",
    "⢻⣇⠹⣷⡀⠀⠀⢀⣾⠇⣼⡟",
    "⠀⠙⠷⣮⣽⣧⣶⠟⣫⠼⠋",
)

# Splash spacing conventions: two-space left margin (matches the previous
# hand-indented splash lines), a gutter between logo and content, and one
# spare cell on the right so cropped output never touches the last column.
_INDENT_WIDTH = 2
_LOGO_GAP_WIDTH = 3
_RIGHT_MARGIN_WIDTH = 1
_MIN_CONTENT_WIDTH = 16

_DESCRIPTION = "open-source SRE agent for automated incident investigation and root cause analysis"


def select_splash_mode(console_width: int) -> SplashMode:
    """Return the splash layout state for a terminal ``console_width`` cells wide."""
    if console_width >= SPLASH_WIDE_MIN_WIDTH:
        return "wide"
    if console_width >= SPLASH_MEDIUM_MIN_WIDTH:
        return "medium"
    return "narrow"


def logo_cell_width(logo_lines: tuple[str, ...]) -> int:
    """Widest logo row measured in display cells (Braille glyphs are 1 cell each)."""
    return max(cell_len(line) for line in logo_lines)


def splash_content_width(console_width: int, logo_lines: tuple[str, ...] | None) -> int:
    """Cells available to the content column beside (or without) the logo."""
    used = _INDENT_WIDTH + _RIGHT_MARGIN_WIDTH
    if logo_lines is not None:
        used += logo_cell_width(logo_lines) + _LOGO_GAP_WIDTH
    return max(console_width - used, _MIN_CONTENT_WIDTH)


def _logo_text(logo_lines: tuple[str, ...]) -> Text:
    """Muted, non-wrapping logo block (SECONDARY reads on light and dark themes)."""
    return Text("\n".join(logo_lines), style=SECONDARY, no_wrap=True, overflow="crop")


def _subtitle_text(version: str) -> Text:
    subtitle = Text(no_wrap=True, overflow="ellipsis")
    subtitle.append("opensre", style=SECONDARY)
    subtitle.append("  ·  ", style=DIM)
    subtitle.append(f"v{version}", style=BRAND)
    return subtitle


def _description_text() -> Text:
    return Text(_DESCRIPTION, style=DIM, overflow="fold")


def build_splash_layout(console_width: int, version: str) -> RenderableType:
    """Build the responsive splash body for ``console_width``."""
    mode = select_splash_mode(console_width)

    if mode == "narrow":
        width = splash_content_width(console_width, None)
        grid = Table.grid(padding=0)
        grid.add_column(width=_INDENT_WIDTH)
        grid.add_column(width=width, justify="left")
        grid.add_row(Text(), _subtitle_text(version))
        grid.add_row(Text(), _description_text())
        return grid

    logo_lines = BRAILLE_LOGO_LARGE if mode == "wide" else BRAILLE_LOGO_SMALL
    content_width = splash_content_width(console_width, logo_lines)

    content = Group(
        _subtitle_text(version),
        Text(),
        _description_text(),
    )

    grid = Table.grid(padding=0)
    grid.add_column(width=_INDENT_WIDTH)
    grid.add_column(width=logo_cell_width(logo_lines), vertical="middle")
    grid.add_column(width=_LOGO_GAP_WIDTH)
    grid.add_column(width=content_width, justify="left", vertical="middle")
    grid.add_row(Text(), _logo_text(logo_lines), Text(), content)
    return grid


__all__ = [
    "BRAILLE_LOGO_LARGE",
    "BRAILLE_LOGO_SMALL",
    "SPLASH_MEDIUM_MIN_WIDTH",
    "SPLASH_WIDE_MIN_WIDTH",
    "SplashMode",
    "build_splash_layout",
    "logo_cell_width",
    "select_splash_mode",
    "splash_content_width",
]
