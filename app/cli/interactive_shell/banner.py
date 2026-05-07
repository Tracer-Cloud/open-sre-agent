"""Splash screen, agent ready-state box, and REPL launch banner.

Three exported entry points
---------------------------
render_splash(console, first_run=False)
    Full branded startup screen with ASCII art and optional security gate.
    Called once when the CLI starts.

render_ready_box(console, session=None)
    BORDER-boxed two-column welcome panel:
      left  → ◉ OpenSRE · provider · model · mode · cwd
      right → "Tips for getting started" + "What's new"
    Called after the splash and on /clear, /welcome, and greeting aliases.

render_banner(console)
    Backward-compatible shim: render_splash + render_ready_box in one call.
    Existing callers (loop.py) continue to work unchanged.

Rendered output legend (colour roles)
--------------------------------------
# [PRIMARY_ALT]  ASCII art lines
# [TEXT_DIM]     "opensre" product name label · cwd · tip / note body
# [ACCENT_SOFT]  version string
# [ACCENT]       "Tips for getting started" / "What's new" headers
# [BORDER]       subtitle description · rule lines · box chrome
# [PRIMARY]      ◉ glyph
# [TEXT]         OpenSRE label + provider/model values
# [WARNING]      read-only or trust-mode notice
"""

from __future__ import annotations

import os
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from app.cli.interactive_shell.theme import (
    ACCENT,
    ACCENT_SOFT,
    BORDER,
    GLYPH_ACTIVE,
    PRIMARY,
    PRIMARY_ALT,
    TEXT,
    TEXT_DIM,
    WARNING,
)
from app.config import LLMSettings
from app.version import get_version

# ── ASCII art ────────────────────────────────────────────────────────────────
# Pre-rendered "big"-style figlet art for "OpenSRE". The figlet output is
# embedded so the tool works without pyfiglet installed. If pyfiglet IS
# available, it renders live (allowing font customisation via OPENSRE_FIGLET_FONT).
#
# Verified ≤ 78 columns (leaving 1-space margin each side inside an 80-col terminal).

_FALLBACK_ART = """\
  ___                    ____  ____  _____
 / _ \\ _ __   ___ _ __  / ___||  _ \\| ____|
| | | | '_ \\ / _ \\ '_ \\ \\___ \\| |_) |  _|
| |_| | |_) |  __/ | | | ___) |  _ <| |___
 \\___/| .__/ \\___|_| |_||____/|_| \\_\\_____|
      |_|"""


def _render_art() -> str:
    """Return figlet art, falling back to the embedded string."""
    font = os.getenv("OPENSRE_FIGLET_FONT", "big")
    try:
        import pyfiglet  # type: ignore[import-untyped,import-not-found]

        rendered: str = pyfiglet.figlet_format("OpenSRE", font=font).rstrip()
        # Reject if it won't fit in 80 columns.
        if rendered and all(len(ln) <= 78 for ln in rendered.splitlines()):
            return rendered
    except Exception:
        # pyfiglet missing or font lookup failed — fall through to ASCII art
        pass
    return _FALLBACK_ART


# ── Provider detection ────────────────────────────────────────────────────────


def resolve_provider_models(settings: object, provider: str) -> tuple[str, str]:
    """Return the active (reasoning_model, toolcall_model) for a provider."""
    if provider in {"codex", "claude-code", "gemini-cli", "cursor", "kimi", "opencode"}:
        env_key = {
            "codex": "CODEX_MODEL",
            "claude-code": "CLAUDE_CODE_MODEL",
            "gemini-cli": "GEMINI_CLI_MODEL",
            "cursor": "CURSOR_MODEL",
            "kimi": "KIMI_MODEL",
            "opencode": "OPENCODE_MODEL",
        }.get(provider, "")
        cli_model = (os.getenv(env_key, "").strip() if env_key else "") or "CLI default"
        return (cli_model, cli_model)

    single_model = str(getattr(settings, f"{provider}_model", "")).strip()
    if single_model:
        return (single_model, single_model)

    reasoning_model = str(getattr(settings, f"{provider}_reasoning_model", "")).strip()
    toolcall_model = str(getattr(settings, f"{provider}_toolcall_model", "")).strip()
    return (reasoning_model or "default", toolcall_model or reasoning_model or "default")


def detect_provider_model() -> tuple[str, str]:
    """Return (provider, model) for the active LLM config."""
    try:
        settings = LLMSettings.from_env()
    except Exception:
        return ("unknown", "unknown")

    provider = settings.provider or os.getenv("LLM_PROVIDER", "anthropic")
    reasoning_model, _toolcall_model = resolve_provider_models(settings, provider)
    return (provider, reasoning_model)


def _is_first_run() -> bool:
    """True when the wizard has never been completed on this machine."""
    try:
        from app.cli.wizard.store import get_store_path

        return not get_store_path().exists()
    except Exception:
        return False


# ── Splash screen ─────────────────────────────────────────────────────────────


def render_splash(console: Console | None = None, *, first_run: bool | None = None) -> None:
    """Print the branded startup splash.

    Rendered output (with colour roles):
    ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ [BORDER divider]
      ___                    ____  ____  _____     [PRIMARY_ALT art]
     / _ \\ _ __   ___ _ __  / ___||  _ \\| ____|
    | | | | '_ \\ / _ \\ '_ \\ \\___ \\| |_) |  _|
    | |_| | |_) |  __/ | | | ___) |  _ <| |___
     \\___/| .__/ \\___|_| |_||____/|_| \\_\\_____|
          |_|
      opensre  [TEXT_DIM]  ·  v2026.4.7 [ACCENT_SOFT]
      open-source SRE agent for automated incident
      investigation and root cause analysis          [BORDER]
    ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ [BORDER divider]

    If first_run (or not set and wizard has never run):
      ⚠  This tool runs AI-powered commands …      [WARNING]
         Press Enter to continue…                   [TEXT_DIM]
    """
    console = console or Console(highlight=False, force_terminal=True, color_system="truecolor")
    if first_run is None:
        first_run = _is_first_run()

    version = get_version()
    art = _render_art()

    console.print()
    console.print(Rule(style=BORDER))
    console.print()

    for line in art.splitlines():
        t = Text()
        t.append("  ")
        t.append(line, style=f"bold {PRIMARY_ALT}")
        console.print(t)

    console.print()

    subtitle = Text()
    subtitle.append("  ")
    subtitle.append("opensre", style=TEXT_DIM)
    subtitle.append("  ·  ", style=BORDER)
    subtitle.append(f"v{version}", style=ACCENT_SOFT)
    console.print(subtitle)

    desc = Text()
    desc.append(
        "  open-source SRE agent for automated incident investigation and root cause analysis",
        style=BORDER,
    )
    console.print(desc)
    console.print()
    console.print(Rule(style=BORDER))

    if first_run:
        console.print()
        notice = Text()
        notice.append("  ")
        notice.append("⚠  ", style=f"bold {WARNING}")
        notice.append(
            "This tool executes AI-powered commands against your infrastructure.\n"
            "     Review the documentation before connecting production systems.\n"
            "     Source: https://github.com/opensre-dev/opensre",
            style=TEXT_DIM,
        )
        console.print(notice)
        console.print()
        if sys.stdin.isatty():
            try:
                console.print(f"  [{TEXT_DIM}]Press Enter to continue…[/]", end="")
                sys.stdin.readline()
            except (EOFError, KeyboardInterrupt, OSError):
                pass
        console.print()


# ── Agent ready-state box ─────────────────────────────────────────────────────

# Static copy for the right column. Keep entries terse — they must read as a
# scannable list, not paragraphs, and fit within ``_RIGHT_COL_WIDTH`` characters
# (the column truncates with `…` past that width). Update _WHATS_NEW with each
# user-visible change worth surfacing on launch.
_TIPS: tuple[str, ...] = (
    "Paste alert JSON or describe an incident",
    "Type /help to list slash commands",
    "Run /doctor for environment diagnostics",
    "Use /investigate <file> for file alerts",
)

_WHATS_NEW: tuple[str, ...] = (
    "Two-column welcome with tips and notes",
    "/welcome reopens this panel anytime",
    "Bare 'agent', 'hi', 'menu' show panel",
)

# Column geometry. Left carries identity + branding and is given more breathing
# room; right is a compact, scannable side-bar that truncates with `…`.
_LEFT_COL_WIDTH = 46
_RIGHT_COL_WIDTH = 40

# OpenSRE brand mark — the figlet "big" O glyph used by the splash wordmark,
# rendered standalone with a thin curved echo tracing its right edge. Each row
# is (body, echo): body renders in the bright brand colour to match the splash,
# echo renders dim so it reads as a shadow/outline rather than a second glyph.
_LOGO_MARK_ROWS: tuple[tuple[str, str], ...] = (
    ("  ___  ", " "),
    (" / _ \\", " \\"),
    ("| |   |", " |"),
    ("| |_  |", " |"),
    (" \\___/", " /"),
)


def _build_logo_mark() -> Text:
    """Return the brand mark as a styled, two-tone Text block."""
    logo = Text(no_wrap=True)
    for index, (body, echo) in enumerate(_LOGO_MARK_ROWS):
        if index:
            logo.append("\n")
        logo.append(body, style=f"bold {PRIMARY_ALT}")
        logo.append(echo, style=TEXT_DIM)
    return logo


def _format_cwd(path: str) -> str:
    """Collapse the user's home directory to ~ for a tidier identity line."""
    home = os.path.expanduser("~")
    if home and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home) :]
    return path


def _build_identity_block(provider: str, model: str, version: str, *, trust_mode: bool) -> Text:
    """Left column: brand mark, branding, version, provider/model, mode, cwd."""
    logo = _build_logo_mark()

    title = Text()
    title.append(f"{GLYPH_ACTIVE} ", style=f"bold {PRIMARY}")
    title.append("OpenSRE", style=f"bold {TEXT}")
    title.append(f"  v{version}", style=ACCENT_SOFT)

    identity = Text()
    identity.append(provider, style=TEXT)
    identity.append("  ·  ", style=BORDER)
    identity.append(model, style=TEXT)

    mode = Text()
    if trust_mode:
        mode.append("trust mode", style=f"bold {WARNING}")
        mode.append("  ·  ", style=BORDER)
        mode.append("approval prompts off", style=TEXT_DIM)
    else:
        mode.append("investigation mode", style=TEXT_DIM)

    cwd = Text(_format_cwd(os.getcwd()), style=TEXT_DIM, overflow="ellipsis", no_wrap=True)

    return Text("\n").join([logo, Text(), title, Text(), identity, mode, cwd])


def _build_notes_block(header_text: str, items: tuple[str, ...]) -> Text:
    """Right column section: bold header followed by dim list items."""
    parts: list[Text] = [Text(header_text, style=f"bold {ACCENT}")]
    for item in items:
        parts.append(Text(item, style=TEXT_DIM, overflow="fold"))
    return Text("\n").join(parts)


def _vertical_divider(height: int) -> Text:
    """Build a single-character vertical rule with ``height`` lines."""
    return Text("\n".join("│" for _ in range(max(height, 1))), style=TEXT_DIM, no_wrap=True)


def render_ready_box(
    console: Console | None = None,
    *,
    session: object = None,
) -> None:
    """Print the BORDER-boxed two-column welcome panel.

    Layout (colour roles in brackets):
    ╭──────────────────────────────────────────────────────────────────────────╮
    │                                                                          │
    │  ◉ OpenSRE  v2026.4.7                  │  Tips for getting started       │
    │                                        │  Paste alert JSON or describe…  │
    │  anthropic  ·  claude-opus-4-5         │  Type /help to list slash …     │
    │  investigation mode                    │  ───                            │
    │  ~/code/opensre                        │  What's new                     │
    │                                        │  Two-column welcome with tips…  │
    │                                                                          │
    ╰──────────────────────────────────────────────────────────────────────────╯

    The left column is weighted wider (identity + branding); the right column
    is a compact side-bar that truncates with `…`.  A dim vertical rule
    divides the two columns to mirror the Claude Code welcome panel.
    """
    console = console or Console(highlight=False, force_terminal=True, color_system="truecolor")
    provider, model = detect_provider_model()
    version = get_version()
    trust_mode: bool = bool(getattr(session, "trust_mode", False))

    left = _build_identity_block(provider, model, version, trust_mode=trust_mode)
    right = Text("\n").join(
        [
            _build_notes_block("Tips for getting started", _TIPS),
            Text("───", style=BORDER),
            _build_notes_block("What's new", _WHATS_NEW),
        ]
    )

    height = max(left.plain.count("\n"), right.plain.count("\n")) + 1
    divider = _vertical_divider(height)

    grid = Table.grid(padding=(0, 2), expand=False)
    grid.add_column(justify="left", vertical="top", width=_LEFT_COL_WIDTH)
    grid.add_column(justify="center", vertical="top", width=1)
    grid.add_column(
        justify="left",
        vertical="top",
        width=_RIGHT_COL_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )
    grid.add_row(left, divider, right)

    console.print()
    console.print(
        Panel(
            grid,
            border_style=BORDER,
            padding=(1, 2),
            expand=False,
            box=box.ROUNDED,
        )
    )
    console.print()


# ── Backward-compatible shim ──────────────────────────────────────────────────


def render_banner(console: Console | None = None) -> None:
    """Render splash + ready-state box in one call (legacy entry point).

    Existing callers (loop.py _repl_main) continue to work unchanged.
    """
    _console = console or Console(highlight=False, force_terminal=True, color_system="truecolor")
    render_splash(_console)
    render_ready_box(_console)
