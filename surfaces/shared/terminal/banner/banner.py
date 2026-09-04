"""Launch banner shared by the CLI landing page and REPL.

Droid-style centered hero: each row is centered independently (a single
``Align.center`` on a multi-line block left-aligns short lines inside the
widest line). Bold block wordmark + version + welcome + capability chips.
"""

from __future__ import annotations

import enum

from rich.align import Align
from rich.cells import cell_len
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.text import Text

from config.constants import PRODUCT_DISPLAY_NAME, WELCOME_DESCRIPTION, WELCOME_TITLE
from config.version import get_opensre_version
from infrastructure.terminal.theme import (
    BOLD_SKILL,
    BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
)
from surfaces.shared.terminal.banner.banner_state import LaunchStatus, load_launch_status
from surfaces.shared.terminal.prompt_layout import clip_prompt_text

_BANNER_VERTICAL_PADDING = 1

#: Version prefix under the wordmark.
_VERSION_PREFIX = "v"
#: Capability-status glyphs: present/usable vs. absent.
_STATUS_OK_GLYPH = "✓"
_STATUS_MISSING_GLYPH = "✗"
#: Spacing between status items.
_STATUS_ITEM_GAP = "     "

#: Minimum console width to paint the ring mark (its cell width + margin);
#: narrower terminals get the compact text title instead.
_WORDMARK_MIN_WIDTH = 24


class LaunchStatusLabel(enum.StrEnum):
    """Labels for the launch banner's capability status line."""

    SKILLS = "Skills"
    INTEGRATIONS = "Integrations"


#: The canonical overlapping-ring OpenSRE mark (docs/images/opensre-mark.svg),
#: rendered in braille — the "loops" logo.
_WORDMARK_ROWS: tuple[str, ...] = (
    "⠀⠀⠀⢀⣤⣶⣾⣿⣿⣿⣿⣶⣦⣄⡈⠒⢦⣄⡀⠀⠀⠀",
    "⠀⢀⣴⣿⠿⠋⢁⣤⣶⠖⠂⠉⠙⢿⣿⣦⠀⠙⣿⣦⡀⠀",
    "⢀⣾⣿⠋⠀⣴⣿⡟⠁⠀⠀⠀⠀⠀⠹⣿⣷⠀⠘⣿⣷⡀",
    "⣼⣿⡇⠀⣼⣿⡿⠀⠀⠀⠀⠀⠀⠀⠀⢹⣿⡇⠀⢹⣿⣇",
    "⣿⣿⠀⠀⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⠀⢸⣿⣿",
    "⣿⣿⠀⠀⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⠀⢸⣿⣿",
    "⢻⣿⣇⠀⢻⣿⣷⠀⠀⠀⠀⠀⠀⠀⠀⣼⣿⡇⠀⣸⣿⡏",
    "⠈⢿⣿⣄⠀⠻⣿⣧⡀⠀⠀⠀⠀⠀⣰⣿⡟⠀⢠⣿⡿⠁",
    "⠀⠈⠻⣿⣷⣄⣈⠛⠻⠶⠄⣀⣤⣾⣿⠟⠀⣰⣿⠟⠀⠀",
    "⠀⠀⠀⠈⠙⠻⠿⣿⣿⣿⡿⠿⠟⠋⠀⠴⠞⠋⠁⠀⠀⠀",
)


def _center(renderable: RenderableType) -> Align:
    """Center one row/block on its own — do not bundle unequal-width lines."""
    return Align.center(renderable)


def _build_wordmark(*, console_width: int) -> Text:
    """Return the bold ring "loops" mark, or a compact title on narrow terminals."""
    if console_width < _WORDMARK_MIN_WIDTH:
        return Text(PRODUCT_DISPLAY_NAME, style=f"bold {HIGHLIGHT}", no_wrap=True)
    return Text("\n".join(_WORDMARK_ROWS), style=f"bold {HIGHLIGHT}", no_wrap=True)


def _append_status_item(
    line: Text,
    label: str,
    count: int | None,
    *,
    available: bool,
) -> None:
    if line:
        line.append(_STATUS_ITEM_GAP, style=DIM)
    line.append(label, style=f"bold {TEXT}")
    if count is not None:
        line.append(f" ({count})", style=SECONDARY)
    glyph = _STATUS_OK_GLYPH if available else _STATUS_MISSING_GLYPH
    # Green success / red missing — same signal language as Droid's chips.
    line.append(f" {glyph}", style=BOLD_SKILL if available else ERROR)


def _build_version_line() -> Text:
    return Text(
        f"{_VERSION_PREFIX}{get_opensre_version()}",
        style=f"bold {BRAND}",
        no_wrap=True,
    )


def _build_welcome_title() -> Text:
    """Accent title — same copy and style as the sign-in screen."""
    return Text(WELCOME_TITLE, style=f"bold {HIGHLIGHT}", no_wrap=True)


def _build_welcome_paragraph() -> Text:
    """One-sentence product description, wrapped and centered (not clipped)."""
    return Text(WELCOME_DESCRIPTION, style=str(TEXT), justify="center")


def _build_capabilities(status: LaunchStatus, *, max_width: int) -> Text:
    capabilities = Text(overflow="fold", no_wrap=True)
    _append_status_item(
        capabilities,
        LaunchStatusLabel.SKILLS,
        status.skill_count,
        available=status.skill_count > 0,
    )
    _append_status_item(
        capabilities,
        LaunchStatusLabel.INTEGRATIONS,
        status.integration_count,
        available=status.integration_count > 0,
    )
    # Clip rather than soft-wrap — a wrapped chip row looks left-ragged.
    plain = capabilities.plain
    if cell_len(plain) > max_width:
        return Text(
            clip_prompt_text(plain, max_width),
            style=str(TEXT),
            no_wrap=True,
        )
    return capabilities


def build_launch_banner(
    console: Console | None = None,
    *,
    session: object = None,
) -> RenderableType:
    """Build the centered, borderless OpenSRE launch banner."""
    del session  # Reserved for future session-scoped launch indicators.
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    status = load_launch_status()
    width = console.width
    # Leave one column empty so the banner never soft-wraps on the last cell.
    line_width = max(width - 1, 1)
    # Rows top-to-bottom (``None`` is a blank spacer). Each is centered on its
    # own axis in the loop below — one Align.center over a multi-line block
    # would left-align the short lines inside the widest one.
    rows: list[RenderableType | None] = [
        _build_wordmark(console_width=width),
        None,
        _build_version_line(),
        None,
        _build_welcome_title(),
        _build_welcome_paragraph(),
        None,
        _build_capabilities(status, max_width=line_width),
    ]
    body: RenderableType = Group(*(Text() if row is None else _center(row) for row in rows))
    return Padding(body, (_BANNER_VERTICAL_PADDING, 0))


def render_launch_banner(
    console: Console | None = None,
    *,
    session: object = None,
) -> None:
    """Print the OpenSRE launch banner."""
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    banner = build_launch_banner(console, session=session)
    console.print(banner)


__all__ = [
    "build_launch_banner",
    "render_launch_banner",
]
