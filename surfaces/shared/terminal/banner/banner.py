"""Compact launch banner shared by the CLI landing page and REPL."""

from __future__ import annotations

import enum

from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from config.constants import PRODUCT_NAME
from config.version import get_opensre_version
from infrastructure.terminal.theme import BRAND, DIM, ERROR, HIGHLIGHT, SECONDARY, TEXT
from surfaces.shared.terminal.banner.banner_state import LaunchStatus, load_launch_status

_SIDE_BY_SIDE_MIN_WIDTH = 72
_BANNER_VERTICAL_PADDING = 1

#: Identity separator and version prefix on the ``opensre · vX`` line.
_IDENTITY_SEPARATOR = "  ·  "
_VERSION_PREFIX = "v"
#: Capability-status glyphs: present/usable vs. absent.
_STATUS_OK_GLYPH = "✓"
_STATUS_MISSING_GLYPH = "✗"
#: Spacing between status items.
_STATUS_ITEM_GAP = "   "


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
    """Return the overlapping-ring OpenSRE mark."""
    return Text("\n".join(_LOGO_ROWS), style=SECONDARY, no_wrap=True)


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
    line.append(f" {glyph}", style=HIGHLIGHT if available else ERROR)


def _build_details(status: LaunchStatus) -> Text:
    """Return the product/version line and compact capability summary."""
    identity = Text(no_wrap=True)
    identity.append(PRODUCT_NAME, style=f"bold {TEXT}")
    identity.append(_IDENTITY_SEPARATOR, style=DIM)
    identity.append(f"{_VERSION_PREFIX}{get_opensre_version()}", style=BRAND)

    capabilities = Text(overflow="fold")
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
    return Text("\n").join([identity, Text(), capabilities])


def build_launch_banner(
    console: Console | None = None,
    *,
    session: object = None,
) -> RenderableType:
    """Build the responsive borderless OpenSRE launch banner."""
    del session  # Reserved for future session-scoped launch indicators.
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    logo = _build_logo_mark()
    details = _build_details(load_launch_status())

    if console.width < _SIDE_BY_SIDE_MIN_WIDTH:
        body: RenderableType = Group(
            Align.center(logo),
            Text(),
            Align.center(details),
        )
    else:
        grid = Table.grid(padding=(0, 5), expand=False)
        grid.add_column(vertical="middle")
        grid.add_column(vertical="middle")
        grid.add_row(logo, details)
        body = Align.center(grid)

    return Padding(body, (_BANNER_VERTICAL_PADDING, 0))


def render_launch_banner(
    console: Console | None = None,
    *,
    session: object = None,
) -> None:
    """Print the compact OpenSRE launch banner."""
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    console.print(build_launch_banner(console, session=session))


__all__ = ["build_launch_banner", "render_launch_banner"]
