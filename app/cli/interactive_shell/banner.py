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

import getpass
import os
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from app.cli.interactive_shell.config import WHATS_NEW
from app.cli.interactive_shell.theme import (
    ACCENT,
    ACCENT_SOFT,
    BORDER,
    PRIMARY,
    PRIMARY_ALT,
    TEXT,
    TEXT_DIM,
    WARNING,
)
from app.config import LLMSettings
from app.version import get_version

# ── Splash art ───────────────────────────────────────────────────────────────
# Pre-rendered by oh-my-logo (devDependency, see package.json) at build time.
# Colour codes are stripped; PRIMARY_ALT (#00E87A) is re-applied at render time.
# Regenerate with: npm run regen-splash  (or: node scripts/regen_splash.js)
#
# SPLASH_ART         oh-my-logo block font --letter-spacing 0 — 59 cols
#                    solid ██ fills + box-drawing inner detail
# SPLASH_ART_NARROW  oh-my-logo simpleBlock font — 72 cols, pure ASCII fallback
# _FALLBACK_ART      original minimal art — 44 cols, absolute last resort

SPLASH_ART = """\
 ██████╗ ██████╗ ███████╗███╗   ██╗███████╗██████╗ ███████╗
██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔════╝██╔══██╗██╔════╝
██║   ██║██████╔╝█████╗  ██╔██╗ ██║███████╗██████╔╝█████╗
██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚════██║██╔══██╗██╔══╝
╚██████╔╝██║     ███████╗██║ ╚████║███████║██║  ██║███████╗
 ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚══════╝"""

SPLASH_ART_NARROW = """\
    _|_|    _|_|_|    _|_|_|_|  _|      _|    _|_|_|  _|_|_|    _|_|_|_|
  _|    _|  _|    _|  _|        _|_|    _|  _|        _|    _|  _|
  _|    _|  _|_|_|    _|_|_|    _|  _|  _|    _|_|    _|_|_|    _|_|_|
  _|    _|  _|        _|        _|    _|_|        _|  _|    _|  _|
    _|_|    _|        _|_|_|_|  _|      _|  _|_|_|    _|    _|  _|_|_|_|"""

_FALLBACK_ART = """\
  ___                    ____  ____  _____
 / _ \\ _ __   ___ _ __  / ___||  _ \\| ____|
| | | | '_ \\ / _ \\ '_ \\ \\___ \\| |_) |  _|
| |_| | |_) |  __/ | | | ___) |  _ <| |___
 \\___/| .__/ \\___|_| |_||____/|_| \\_\\_____|
      |_|"""


def _render_art(console_width: int = 80) -> str:
    """Return the splash art string for the given terminal width.

    Priority: SPLASH_ART (grid, 34 cols) → SPLASH_ART_NARROW (simpleBlock, 72 cols)
    → _FALLBACK_ART (minimal, 44 cols).  OPENSRE_FIGLET_FONT overrides the default
    when pyfiglet is installed.
    """
    custom_font = os.getenv("OPENSRE_FIGLET_FONT")
    if custom_font:
        try:
            import pyfiglet  # type: ignore[import-untyped,import-not-found]

            rendered: str = pyfiglet.figlet_format("OpenSRE", font=custom_font).rstrip()
            if rendered and all(len(ln) <= console_width - 2 for ln in rendered.splitlines()):
                return rendered
        except Exception:
            # pyfiglet missing or font lookup failed — fall through to ASCII art
            pass

    art_width = max(len(ln) for ln in SPLASH_ART.splitlines())
    narrow_width = max(len(ln) for ln in SPLASH_ART_NARROW.splitlines())
    fallback_width = max(len(ln) for ln in _FALLBACK_ART.splitlines())

    if console_width >= art_width + 4:
        return SPLASH_ART
    if console_width >= narrow_width + 4:
        return SPLASH_ART_NARROW
    if console_width >= fallback_width + 4:
        return _FALLBACK_ART
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
    ╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋           [PRIMARY_ALT art]
    ╋┏━━┓╋┏━━┓╋┏━━┓╋┏━┓╋╋┏━━┓╋┏━┓╋┏━━┓
    ...
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
    art = _render_art(console.width)

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
                # Non-interactive stdin or user abort — skip blocking and continue startup.
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

# Column geometry. Left carries identity + branding and is given more breathing
# room; right is a compact, scannable side-bar that truncates with `…`.
_LEFT_COL_WIDTH = 34
_RIGHT_COL_WIDTH = 52

# OpenSRE brand mark — single "O" from oh-my-logo tiny font (half-block chars).
_LOGO_MARK_ROWS: tuple[tuple[str, str], ...] = (
    ("█▀█", ""),
    ("█▄█", ""),
)


def _get_username() -> str:
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
        logo.append(body, style=f"bold {PRIMARY_ALT}")
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
    identity.append(model, style=f"bold {ACCENT}")
    identity.append("  ·  ", style=BORDER)
    if trust_mode:
        identity.append(tier, style=f"bold {WARNING}")
        identity.append("  ·  ", style=BORDER)
    else:
        identity.append(tier, style=TEXT_DIM)
        identity.append("  ·  ", style=BORDER)
    identity.append(cwd, style=TEXT_DIM)

    return Text("\n").join([logo, Text(), Text(), greeting, Text(), Text(), identity])


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
    """Print the two-column welcome panel with an embedded title bar.

    Layout:
    ── OpenSRE · v2026.4.5 ─────────────────────────────────────────────────╮
    │                                                                         │
    │      Welcome back paul!          │  Tips for getting started            │
    │           █▀█                   │  Paste alert JSON or describe…        │
    │           █▄█                   │  ───                                  │
    │                                  │  What's new                          │
    │  claude-opus-4-7  ·  anthropic  │  Two-column welcome with tips…        │
    │  · ~/code/opensre                │  /release-notes for more             │
    │                                                                         │
    ╰─────────────────────────────────────────────────────────────────────────╯
    """
    console = console or Console(highlight=False, force_terminal=True, color_system="truecolor")
    provider, model = detect_provider_model()
    version = get_version()
    trust_mode: bool = bool(getattr(session, "trust_mode", False))

    # Step 1 — embedded title bar
    panel_title = Text()
    panel_title.append(" OpenSRE", style=f"bold {PRIMARY}")
    panel_title.append(" · ", style=BORDER)
    panel_title.append(f"v{version} ", style=ACCENT_SOFT)

    # Step 2 — greeting + centred mascot + flowing identity (no version repeated)
    left = _build_identity_block(provider, model, trust_mode=trust_mode)

    right = Text("\n").join(
        [
            _build_notes_block("Tips for getting started", _TIPS),
            Text("───", style=BORDER),
            _build_notes_block("What's new", WHATS_NEW),
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
            title=panel_title,
            title_align="left",
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
