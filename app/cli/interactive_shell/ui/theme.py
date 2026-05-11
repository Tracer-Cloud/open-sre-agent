"""Shared color theme for the OpenSRE CLI.

Single source of truth for every colour rendered to the terminal. Eight
semantic tokens — never introduce new hexes, never use Rich named colours
(red / yellow / cyan / ...), never embed raw ANSI colour escapes outside
this module.

Token reference
---------------
  HIGHLIGHT  brand name, ❯ prompt, ✓ success, /commands, key findings, live indicator
  BRAND      model name, file paths, version numbers, secondary labels
  TEXT       all primary body text, step names, values, section headers
  SECONDARY  tips, descriptions, muted info, secondary body text
  DIM        timestamps, dividers, labels, ruled-out items, dim context
  WARNING    warnings only — no auth, fallback store, config issues
  ERROR      errors only — missing required config, failures
  BG         terminal background, never used as foreground

Usage
-----
  from app.cli.interactive_shell.ui.theme import HIGHLIGHT, ERROR, DIM
  console.print(f"[{HIGHLIGHT}]✓ success[/]")
  console.print(f"[{ERROR}]✗ failed[/]")
"""

from __future__ import annotations

from rich.theme import Theme

# ── Semantic color tokens (the only permitted colours) ─────────────────────

HIGHLIGHT = "#B9EDAF"
BRAND = "#66A17D"
TEXT = "#E0E0E0"
SECONDARY = "#888888"
DIM = "#444444"
WARNING = "#CEA25C"
ERROR = "#C45B52"
BG = "#0A0A0A"

# ── Rich style shorthands (bold variants of the semantic tokens) ──────────

BOLD_HIGHLIGHT = f"bold {HIGHLIGHT}"
BOLD_BRAND = f"bold {BRAND}"
BOLD_TEXT = f"bold {TEXT}"
BOLD_WARNING = f"bold {WARNING}"
BOLD_ERROR = f"bold {ERROR}"

# ── Semantic glyphs ────────────────────────────────────────────────────────

GLYPH_SUCCESS = "✓"
GLYPH_WARNING = "⚠"
GLYPH_ERROR = "✗"
GLYPH_PROMPT = "?"
GLYPH_ACTIVE = "◉"
GLYPH_BULLET = "·"

# ── ANSI escape sequences for prompt_toolkit (bypasses Rich markup) ────────
# This module is the only place in the codebase where raw ANSI escapes are
# permitted. Every truecolour value below corresponds to one of the eight
# semantic tokens above.

_HIGHLIGHT_RGB = (0xB9, 0xED, 0xAF)
_BRAND_RGB = (0x66, 0xA1, 0x7D)
_TEXT_RGB = (0xE0, 0xE0, 0xE0)
_DIM_RGB = (0x44, 0x44, 0x44)
_BG_RGB = (0x0A, 0x0A, 0x0A)


def _fg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


HIGHLIGHT_ANSI = _fg(_HIGHLIGHT_RGB)
BRAND_ANSI = _fg(_BRAND_RGB)
TEXT_ANSI = _fg(_TEXT_RGB)
DIM_ANSI = _fg(_DIM_RGB)
BOLD_BRAND_ANSI = f"\x1b[1m{BRAND_ANSI}"

ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"

PROMPT_ACCENT_ANSI = f"\x1b[1;38;2;{_HIGHLIGHT_RGB[0]};{_HIGHLIGHT_RGB[1]};{_HIGHLIGHT_RGB[2]}m"
PROMPT_FRAME_ANSI = PROMPT_ACCENT_ANSI

DIM_COUNTER_ANSI = DIM_ANSI
SURFACE_BG_ANSI = f"\x1b[48;2;{_BG_RGB[0]};{_BG_RGB[1]};{_BG_RGB[2]}m"

# Input box surface — slightly lighter than BG so the full-width fill is visible.
_INPUT_SURFACE_RGB = (0x14, 0x14, 0x14)
INPUT_SURFACE = "#141414"
INPUT_SURFACE_BG_ANSI = (
    f"\x1b[48;2;{_INPUT_SURFACE_RGB[0]};{_INPUT_SURFACE_RGB[1]};{_INPUT_SURFACE_RGB[2]}m"
)

# Inline REPL picker: full-line selection bar (HIGHLIGHT fg over INPUT_SURFACE bg).
MENU_SELECTION_ROW_ANSI = f"{INPUT_SURFACE_BG_ANSI}\x1b[1m{HIGHLIGHT_ANSI}"

# ── Rich Theme override for Markdown rendering ─────────────────────────────
# Overrides Rich's defaults ("bold cyan on black" / "cyan on black") so that
# inline code spans and code-block chrome stay within the project palette.
MARKDOWN_THEME = Theme(
    {
        "markdown.code": f"bold {HIGHLIGHT}",
        "markdown.code_block": TEXT,
        "markdown.h1": f"bold {HIGHLIGHT}",
        "markdown.h2": f"bold {BRAND}",
        "markdown.h3": f"bold {BRAND}",
    }
)
