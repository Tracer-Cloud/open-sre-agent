"""Launch banner shared by the CLI landing page and REPL.

Centered hero (mark + identity + tip + shortcuts + capability chips) so the
first viewport reads like a shipped product, not a left-aligned school demo.
"""

from __future__ import annotations

import enum

from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.text import Text

from config.constants import PRODUCT_NAME
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

_BANNER_VERTICAL_PADDING = 1

#: Version prefix under the product name.
_VERSION_PREFIX = "v"
#: Capability-status glyphs: present/usable vs. absent.
_STATUS_OK_GLYPH = "✓"
_STATUS_MISSING_GLYPH = "✗"
#: Spacing between status items.
_STATUS_ITEM_GAP = "     "

#: One tip under the identity — mirrors Droid's launch tip, OpenSRE-specific.
_TIP_BODY = "try /theme for a new look · Ctrl+O expands long tool output"
#: Keyboard hints (real bindings, not aspirational shortcuts).
_SHORTCUTS_LINE = "/ commands · tab tool details · ? help · Enter send"


class LaunchStatusLabel(enum.StrEnum):
    """Labels for the launch banner's capability status line."""

    SKILLS = "Skills"
    INTEGRATIONS = "Integrations"


#: Braille rendering of the canonical OpenSRE mark (docs/images/opensre-mark.svg).
_LOGO_ROWS: tuple[str, ...] = (
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


def _build_logo_mark() -> Text:
    """Return the overlapping-ring OpenSRE mark in a bright, confident accent.

    Bold ``HIGHLIGHT`` (not dim grey) so the hero reads crisp and high-contrast
    like a landing screen, rather than washed-out.
    """
    return Text("\n".join(_LOGO_ROWS), style=f"bold {HIGHLIGHT}", no_wrap=True)


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


def _build_identity() -> Text:
    """Centered product name + clean marketing version (no build tail)."""
    name = Text(PRODUCT_NAME, style=f"bold {TEXT}", justify="center")
    version = Text(
        f"{_VERSION_PREFIX}{get_opensre_version()}",
        style=str(BRAND),
        justify="center",
    )
    return Text("\n").join([name, version])


def _build_tip_block() -> Text:
    tip = Text(no_wrap=False)
    tip.append("TIP", style=f"bold {HIGHLIGHT}")
    tip.append("  ", style=DIM)
    tip.append(_TIP_BODY, style=str(SECONDARY))
    shortcuts = Text(_SHORTCUTS_LINE, style=str(DIM))
    return Text("\n").join([tip, shortcuts])


def _build_capabilities(status: LaunchStatus) -> Text:
    capabilities = Text(overflow="fold", justify="center")
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
    return capabilities


def _build_details(status: LaunchStatus) -> Text:
    """Return identity + tip + capability summary (tests assert version here)."""
    return Text("\n").join(
        [
            _build_identity(),
            Text(),
            _build_tip_block(),
            Text(),
            _build_capabilities(status),
        ]
    )


def build_launch_banner(
    console: Console | None = None,
    *,
    session: object = None,
) -> RenderableType:
    """Build the centered, borderless OpenSRE launch banner."""
    del session  # Reserved for future session-scoped launch indicators.
    del console  # Width no longer switches layout; always stack/center.
    logo = _build_logo_mark()
    details = _build_details(load_launch_status())
    body: RenderableType = Group(
        Align.center(logo),
        Text(),
        Align.center(details),
    )
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
    console.print(build_launch_banner(console, session=session))


__all__ = ["build_launch_banner", "render_launch_banner"]
