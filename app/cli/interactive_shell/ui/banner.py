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
# [WARNING]    read-only or trust-mode notice
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

from app.cli.interactive_shell.config import WHATS_NEW
from app.cli.interactive_shell.ui.theme import (
    BRAND,
    DIM,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)
from app.config import LLMSettings
from app.utils.figlet import render_figlet
from app.version import get_version

# ── Splash art ───────────────────────────────────────────────────────────────
# Pre-rendered during development and checked into this module as a static string.
# Colour codes are stripped; HIGHLIGHT is re-applied at render time.
#
# SPLASH_ART         block font, 59 cols, solid ██ fills
# SPLASH_ART_NARROW  simpleBlock font, 72 cols, pure ASCII fallback
# _FALLBACK_ART      minimal art, 44 cols, last resort

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
        rendered = render_figlet("OpenSRE", font=custom_font, max_line_width=console_width - 2)
        if rendered:
            return rendered

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
    if provider in {
        "codex",
        "claude-code",
        "gemini-cli",
        "antigravity-cli",
        "cursor",
        "kimi",
        "opencode",
    }:
        env_key = {
            "codex": "CODEX_MODEL",
            "claude-code": "CLAUDE_CODE_MODEL",
            "gemini-cli": "GEMINI_CLI_MODEL",
            "antigravity-cli": "ANTIGRAVITY_CLI_MODEL",
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
    ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ [DIM divider]
    ╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋╋           [HIGHLIGHT art]
    ╋┏━━┓╋┏━━┓╋┏━━┓╋┏━┓╋╋┏━━┓╋┏━┓╋┏━━┓
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

# Display-name overrides for known integration service slugs.
_SERVICE_DISPLAY_NAMES: dict[str, str] = {
    "grafana": "Grafana",
    "datadog": "Datadog",
    "honeycomb": "Honeycomb",
    "coralogix": "Coralogix",
    "aws": "AWS",
    "github": "GitHub",
    "sentry": "Sentry",
    "prometheus": "Prometheus",
    "loki": "Loki",
    "elasticsearch": "Elasticsearch",
    "bigquery": "BigQuery",
    "pagerduty": "PagerDuty",
    "slack": "Slack",
    "telegram": "Telegram",
    "signoz": "SigNoz",
    "jira": "Jira",
    "gitlab": "GitLab",
    "vercel": "Vercel",
    "mongodb": "MongoDB",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "redis": "Redis",
    "kafka": "Kafka",
    "rabbitmq": "RabbitMQ",
    "clickhouse": "ClickHouse",
    "mariadb": "MariaDB",
    "kubernetes": "Kubernetes",
    "betterstack": "Better Stack",
    "snowflake": "Snowflake",
    "newrelic": "New Relic",
    "opsgenie": "OpsGenie",
    "linear": "Linear",
    "supabase": "Supabase",
}


def _load_configured_integrations() -> list[str]:
    """Return display names for integrations currently configured via env vars. Never raises."""
    try:
        from app.integrations.catalog import (  # lazy — avoids circular deps
            configured_integration_services,
        )

        return [
            _SERVICE_DISPLAY_NAMES.get(service, service.title())
            for service in configured_integration_services()
        ]
    except Exception:
        return []


def _is_alert_listener_active() -> bool:
    """Return True if the alert listener is enabled in config. Never raises."""
    try:
        from app.cli.interactive_shell.config import ReplConfig

        return ReplConfig.load().alert_listener_enabled
    except Exception:
        return False


def _build_ambient_right_column(session: object = None) -> Text:
    """Right column for returning users: live integration status and alert listener state."""
    parts: list[Text] = []

    # Integrations
    parts.append(Text("Integrations", style=f"bold {BRAND}"))
    names = _load_configured_integrations()
    if names:
        _MAX_SHOWN = 6
        shown = names[:_MAX_SHOWN]
        overflow = len(names) - len(shown)
        name_line = Text(overflow="fold")
        for idx, name in enumerate(shown):
            if idx:
                name_line.append("  ·  ", style=DIM)
            name_line.append(name, style=SECONDARY)
        if overflow:
            name_line.append(f"  +{overflow}", style=DIM)
        parts.append(name_line)
    else:
        parts.append(Text("run /onboard to connect tools", style=DIM))

    parts.append(Text("───", style=DIM))

    # Alert listener
    parts.append(Text("Alert listener", style=f"bold {BRAND}"))
    if _is_alert_listener_active():
        listener_line = Text()
        listener_line.append("● ", style=f"bold {HIGHLIGHT}")
        listener_line.append("active", style=SECONDARY)
        parts.append(listener_line)
    else:
        parts.append(Text("○  not configured", style=DIM))

    # Session summary — only shown when /clear is used mid-session with history
    if session is not None:
        history: list[object] = getattr(session, "history", [])
        if history:
            parts.append(Text("───", style=DIM))
            parts.append(Text("This session", style=f"bold {BRAND}"))
            count = len(history)
            noun = "interaction" if count == 1 else "interactions"
            parts.append(Text(f"{count} {noun}", style=SECONDARY))

    return Text("\n").join(parts)


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
    """Print the two-column welcome panel with an embedded title bar.

    Layout:
    ── OpenSRE · v<version> ────────────────────────────────────────────────╮
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
