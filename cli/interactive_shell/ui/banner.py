"""Splash screen, agent ready-state box, and REPL launch banner.

Three exported entry points
---------------------------
render_splash(console, first_run=False)
    Full branded startup screen with ASCII art and optional security gate.
    Called once when the CLI starts.

render_ready_box(console, session=None)
    DIM-bordered two-column welcome panel:
      left  → ◉ OpenSRE · provider · model · mode · cwd
      right → "Tips for getting started" + "What's new"
    Called after the splash and on /clear, /welcome, and greeting aliases.

render_banner(console)
    Backward-compatible shim: render_splash + render_ready_box in one call.
    Existing callers (loop.py) continue to work unchanged.

Rendered output legend (colour roles)
--------------------------------------
# [HIGHLIGHT]  ASCII art lines · ◉ glyph · OpenSRE brand name
# [BRAND]      version string · model name · section headers
# [SECONDARY]  "opensre" product name label · cwd · tip / note body
# [DIM]        subtitle description · rule lines · box chrome · dividers
# [TEXT]       provider/model values · greeting
# [WARNING]    read-only or trust-mode notice · incomplete-integration marker
"""

from __future__ import annotations

import getpass
import math
import os
import sys

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from cli.config import WHATS_NEW
from cli.interactive_shell.ui.banner_art import _render_art
from cli.interactive_shell.ui.banner_state import _build_ambient_right_column
from cli.interactive_shell.ui.provider import detect_provider_model
from cli.interactive_shell.ui.theme import (
    BRAND,
    DIM,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)
from config.version import get_version


def _is_first_run() -> bool:
    """True when the wizard has never been completed on this machine."""
    try:
        from cli.wizard.store import get_store_path

        return not get_store_path().exists()
    except Exception:
        return False


# ── Splash screen ─────────────────────────────────────────────────────────────


def render_splash(console: Console | None = None, *, first_run: bool | None = None) -> None:
    """Print the branded startup splash.

    Rendered output (with colour roles):
    ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ [DIM divider]
    ╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋           [HIGHLIGHT art]
    ...
      opensre  [SECONDARY]  ·  v<version> [BRAND]
      open-source SRE agent for automated incident
      investigation and root cause analysis          [DIM]
    ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ [DIM divider]

    If first_run (or not set and wizard has never run):
      ⚠  This tool runs AI-powered commands …      [WARNING]
         Press Enter to continue…                   [SECONDARY]
    """
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    if first_run is None:
        first_run = _is_first_run()

    version = get_version()
    art = _render_art(console.width)

    console.print()
    console.print(Rule(style=DIM))
    console.print()

    for line in art.splitlines():
        t = Text()
        t.append("  ")
        for ch in line:
            t.append(ch, style=f"bold {HIGHLIGHT}" if ch == "█" else f"bold {BRAND}")
        console.print(t)

    console.print()

    subtitle = Text()
    subtitle.append("  ")
    subtitle.append("opensre", style=SECONDARY)
    subtitle.append("  ·  ", style=DIM)
    subtitle.append(f"v{version}", style=BRAND)
    console.print(subtitle)

    desc = Text()
    desc.append(
        "  open-source SRE agent for automated incident investigation and root cause analysis",
        style=DIM,
    )
    console.print(desc)
    console.print()
    console.print(Rule(style=DIM))

    if first_run:
        console.print()
        notice = Text()
        notice.append("  ")
        notice.append("⚠  ", style=f"bold {WARNING}")
        notice.append(
            "This tool executes AI-powered commands against your infrastructure.\n"
            "     Review the documentation before connecting production systems.\n"
            "     Source: https://github.com/opensre-dev/opensre",
            style=SECONDARY,
        )
        console.print(notice)
        console.print()
        if sys.stdin.isatty():
            try:
                console.print(f"  [{SECONDARY}]Press Enter to continue…[/]", end="")
                sys.stdin.readline()
            except (EOFError, KeyboardInterrupt, OSError):
                # Non-interactive stdin or user abort — skip blocking and continue startup.
                pass
        console.print()


# ── Agent ready-state box ─────────────────────────────────────────────────────

# Static copy for the right column (first-run only). Keep entries terse.
_TIPS: tuple[str, ...] = (
    "Paste alert JSON or describe an incident",
    "Type /help to list slash commands",
    "Run /doctor for environment diagnostics",
    "Use /investigate for runnable demos/templates",
)

# Panel geometry. The body switches to a stacked layout on narrow terminals,
# and otherwise expands to fill the full console width while keeping the left
# identity column readable and the right notes column roomy.
_MIN_LEFT_COL_WIDTH = 34
_MAX_LEFT_COL_WIDTH = 48
_MIN_RIGHT_COL_WIDTH = 40
_DIVIDER_WIDTH = 3
_PANEL_PADDING_X = 2
_PANEL_FRAME_WIDTH = 2 + (_PANEL_PADDING_X * 2)
_MIN_TWO_COLUMN_CONTENT_WIDTH = _MIN_LEFT_COL_WIDTH + _DIVIDER_WIDTH + _MIN_RIGHT_COL_WIDTH

# OpenSRE brand mark — single "O" from oh-my-logo tiny font (half-block chars).
_LOGO_MARK_ROWS: tuple[tuple[str, str], ...] = (
    ("█▀█", ""),
    ("█▄█", ""),
)


def _github_username() -> str:
    """Return the saved GitHub login for the configured GitHub integration, or "".

    Best-effort and never raises: the welcome greeting must render even when the
    integration store is unreadable or GitHub is not configured.
    """
    try:
        from integrations.github_identity import saved_github_username

        return saved_github_username()
    except Exception:
        return ""


def _get_username() -> str:
    # Prefer the authenticated GitHub handle once it is known, so the greeting
    # reflects the user's GitHub identity rather than the local system account.
    github = _github_username()
    if github:
        return github
    try:
        return getpass.getuser()
    except Exception:
        return "there"


def _build_logo_mark() -> Text:
    """Return the brand mark left-aligned (flush with the column's 2-space indent)."""
    logo = Text(no_wrap=True)
    for index, (body, _echo) in enumerate(_LOGO_MARK_ROWS):
        if index:
            logo.append("\n")
        logo.append(body, style=f"bold {HIGHLIGHT}")
    return logo


def _format_cwd(path: str) -> str:
    """Collapse the user's home directory to ~ for a tidier identity line."""
    home = os.path.expanduser("~")
    if home and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home) :]
    return path


def _build_identity_block(provider: str, model: str, *, trust_mode: bool) -> Text:
    """Left column: mascot · blank · greeting · blank · identity line (all left-aligned)."""
    logo = _build_logo_mark()

    greeting = Text()
    greeting.append(f"Welcome back {_get_username()}!", style=f"bold {TEXT}")

    # Single flowing line: model · tier · workspace
    cwd = _format_cwd(os.getcwd())
    tier = "trust mode" if trust_mode else provider
    identity = Text(overflow="fold")
    identity.append(model, style=f"bold {BRAND}")
    identity.append("  ·  ", style=DIM)
    if trust_mode:
        identity.append(tier, style=f"bold {WARNING}")
        identity.append("  ·  ", style=DIM)
    else:
        identity.append(tier, style=SECONDARY)
        identity.append("  ·  ", style=DIM)
    identity.append(cwd, style=SECONDARY)

    return Text("\n").join([logo, Text(), Text(), greeting, Text(), Text(), identity])


def _build_notes_block(header_text: str, items: tuple[str, ...]) -> Text:
    """Right column section: bold header followed by dim list items."""
    parts: list[Text] = [Text(header_text, style=f"bold {BRAND}")]
    for item in items:
        parts.append(Text(item, style=SECONDARY, overflow="fold"))
    return Text("\n").join(parts)


def _visual_line_count(block: Text, width: int) -> int:
    """Estimate how many terminal lines a Text block will occupy at ``width``."""
    safe_width = max(width, 1)
    total = 0
    for raw_line in block.plain.split("\n"):
        total += max(1, math.ceil(max(len(raw_line), 1) / safe_width))
    return total


def _vertical_divider(height: int) -> Text:
    """Build a padded vertical rule with ``height`` lines."""
    return Text("\n".join(" │ " for _ in range(max(height, 1))), style=DIM, no_wrap=True)


def _two_column_widths(console_width: int) -> tuple[int, int]:
    """Return responsive left/right widths for the ready panel body."""
    content_width = max(console_width - _PANEL_FRAME_WIDTH, _MIN_TWO_COLUMN_CONTENT_WIDTH)
    left_width = int((content_width - _DIVIDER_WIDTH) * 0.42)
    left_width = max(_MIN_LEFT_COL_WIDTH, min(left_width, _MAX_LEFT_COL_WIDTH))
    right_width = content_width - _DIVIDER_WIDTH - left_width
    if right_width < _MIN_RIGHT_COL_WIDTH:
        right_width = _MIN_RIGHT_COL_WIDTH
        left_width = content_width - _DIVIDER_WIDTH - right_width
    return left_width, right_width


def build_ready_panel(
    console: Console | None = None,
    *,
    session: object = None,
) -> Panel:
    """Build the responsive welcome panel shared by startup and CLI help."""
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    provider, model = detect_provider_model()
    version = get_version()
    trust_mode: bool = bool(getattr(session, "trust_mode", False))

    panel_title = Text()
    panel_title.append(" OpenSRE", style=f"bold {HIGHLIGHT}")
    panel_title.append(" · ", style=DIM)
    panel_title.append(f"v{version} ", style=BRAND)

    left = _build_identity_block(provider, model, trust_mode=trust_mode)
    if _is_first_run():
        right = Text("\n").join(
            [
                _build_notes_block("Tips for getting started", _TIPS),
                Text("───", style=DIM),
                _build_notes_block("What's new", WHATS_NEW),
            ]
        )
    else:
        right = _build_ambient_right_column(session=session)

    body: Group | Table
    if console.width - _PANEL_FRAME_WIDTH >= _MIN_TWO_COLUMN_CONTENT_WIDTH:
        left_width, right_width = _two_column_widths(console.width)
        height = max(
            _visual_line_count(left, left_width),
            _visual_line_count(right, right_width),
        )
        divider = _vertical_divider(height)

        grid = Table.grid(padding=0, expand=False)
        grid.add_column(justify="left", vertical="top", width=left_width)
        grid.add_column(justify="center", vertical="top", width=_DIVIDER_WIDTH)
        grid.add_column(justify="left", vertical="top", width=right_width)
        grid.add_row(left, divider, right)
        body = grid
    else:
        body = Group(
            left,
            Rule(style=DIM),
            right,
        )

    return Panel(
        body,
        title=panel_title,
        title_align="left",
        border_style=DIM,
        padding=(1, _PANEL_PADDING_X),
        expand=True,
        box=box.ROUNDED,
    )


def render_ready_box(
    console: Console | None = None,
    *,
    session: object = None,
) -> None:
    """Print the two-column welcome panel with an embedded title bar."""
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    console.print()
    console.print(build_ready_panel(console, session=session))
    console.print()


# ── Backward-compatible shim ──────────────────────────────────────────────────


def render_banner(console: Console | None = None) -> None:
    """Render splash + ready-state box in one call (legacy entry point).

    Existing callers (runtime.entrypoint.repl_main) continue to work unchanged.
    """
    _console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    render_splash(_console)
    render_ready_box(_console)
