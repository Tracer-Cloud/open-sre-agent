"""Shared color theme for the interactive shell.

Design system palette — use these role constants via Rich markup or as hex
strings in Rich styles. Never use raw \\033[3Xm / \\033[9Xm ANSI codes in
user-facing output; every colour must carry a semantic meaning per the role
table below.

Usage
-----
  from app.cli.interactive_shell.theme import PRIMARY, ERROR, TEXT_DIM
  console.print(f"[{PRIMARY}]✓ success[/]")
  console.print(f"[{ERROR}]✗ failed[/]")
"""

from __future__ import annotations

# ── Semantic color roles ────────────────────────────────────────────────────

PRIMARY = "#1AFF8C"  # prompts (?), success (✓), active selections
PRIMARY_ALT = "#00E87A"  # splash art, section headers
ACCENT = "#00D4C8"  # file paths, commands, runnable hints
ACCENT_SOFT = "#5EF0E8"  # version numbers, highlights
ACCENT_DIM = "#00A89E"  # slash commands, muted interactive elements
WARNING = "#F0A500"  # warnings, read-only mode
WARNING_ALT = "#FF8C42"  # elevated warnings, destructive confirmations
ERROR = "#FF4D6A"  # errors, failures, ✗ checks
ERROR_ALT = "#E8365D"  # critical errors only
TEXT = "#E8EFE8"  # primary body text, values
TEXT_DIM = "#6B8C6B"  # labels, secondary info, key names
BORDER = "#2D4A2D"  # box borders, dividers, rule lines
SURFACE = "#111811"  # titlebar, inset panels
BG = "#0E0E0E"  # terminal background (never set directly)

# ── Rich style shorthands ──────────────────────────────────────────────────

BOLD_PRIMARY = f"bold {PRIMARY}"
BOLD_ACCENT = f"bold {ACCENT}"
BOLD_TEXT = f"bold {TEXT}"
BOLD_ERROR = f"bold {ERROR}"
BOLD_WARNING = f"bold {WARNING}"

# ── Semantic glyphs ────────────────────────────────────────────────────────

GLYPH_SUCCESS = "✓"
GLYPH_WARNING = "⚠"
GLYPH_ERROR = "✗"
GLYPH_PROMPT = "?"
GLYPH_ACTIVE = "◉"
GLYPH_BULLET = "·"

# ── ANSI escape sequences for prompt_toolkit (bypasses Rich markup) ────────

_PRIMARY_RGB = (0x1A, 0xFF, 0x8C)
_TEXT_DIM_RGB = (0x6B, 0x8C, 0x6B)

PROMPT_ACCENT_ANSI = f"\x1b[1;38;2;{_PRIMARY_RGB[0]};{_PRIMARY_RGB[1]};{_PRIMARY_RGB[2]}m"
ANSI_RESET = "\x1b[0m"

# Truecolour ANSI for the bracketed turn counter in the prompt.
DIM_COUNTER_ANSI = f"\x1b[38;2;{_TEXT_DIM_RGB[0]};{_TEXT_DIM_RGB[1]};{_TEXT_DIM_RGB[2]}m"

# ── Backward-compatible aliases (referenced by existing modules) ───────────
# Do not remove — update the importing modules gradually instead.

SEPARATOR_COLOR = BORDER
TERMINAL_ERROR = ERROR
TERMINAL_ACCENT = ACCENT
TERMINAL_ACCENT_BOLD = BOLD_ACCENT
DIM_TEXT_COLOR = TEXT_DIM

BANNER_PRIMARY = PRIMARY
BANNER_SECONDARY = PRIMARY_ALT
BANNER_TERTIARY = ACCENT_SOFT
BANNER_UI_DIVIDER = BORDER

# OpenClaw legacy names kept for loop.py completion-menu styles.
OPENCLAW_CORAL = ACCENT  # border colour → ACCENT
OPENCLAW_ORANGE = PRIMARY  # active completion → PRIMARY
OPENCLAW_AMBER = ACCENT_SOFT  # slash-command token → ACCENT_SOFT
