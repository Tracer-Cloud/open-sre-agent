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
  INPUT_SURFACE  prompt/menu surface background

Optional per-theme splash gradient (``CliTheme`` only)
----------------------------------------------------
  SPLASH_GRADIENT_START  left/start colour for splash ASCII art gradients
  SPLASH_GRADIENT_END    right/end colour for splash ASCII art gradients

When both gradient fields are set, the splash banner interpolates between
them across each row of ``█`` block characters. Themes without these fields
continue to use ``HIGHLIGHT`` for splash art.

Usage
-----
  from platform.terminal.theme import HIGHLIGHT, ERROR, DIM
  console.print(f"[{HIGHLIGHT}]✓ success[/]")
  console.print(f"[{ERROR}]✗ failed[/]")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import SupportsIndex

from rich.theme import Theme


@dataclass(frozen=True)
class CliTheme:
    """Named palette for interactive shell rendering."""

    name: str
    HIGHLIGHT: str
    BRAND: str
    TEXT: str
    SECONDARY: str
    DIM: str
    WARNING: str
    ERROR: str
    BG: str
    INPUT_SURFACE: str
    SPLASH_GRADIENT_START: str | None = None
    SPLASH_GRADIENT_END: str | None = None


THEME_REGISTRY: dict[str, CliTheme] = {
    "green": CliTheme(
        name="green",
        HIGHLIGHT="#B9EDAF",
        BRAND="#66A17D",
        TEXT="#E0E0E0",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#CEA25C",
        ERROR="#C45B52",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "blue": CliTheme(
        name="blue",
        HIGHLIGHT="#A8D4FF",
        BRAND="#6FA5D8",
        TEXT="#E0E0E0",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#D8B06F",
        ERROR="#CF6B63",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "amber": CliTheme(
        name="amber",
        HIGHLIGHT="#F2D48A",
        BRAND="#C99944",
        TEXT="#E0E0E0",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#E0B466",
        ERROR="#CF6B63",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "mono": CliTheme(
        name="mono",
        HIGHLIGHT="#C6C6C6",
        BRAND="#A7A7A7",
        TEXT="#E0E0E0",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#B0B0B0",
        ERROR="#8E8E8E",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "red": CliTheme(
        name="red",
        HIGHLIGHT="#FF9E8A",
        BRAND="#C45B52",
        TEXT="#E0E0E0",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#E0B466",
        ERROR="#CF6B63",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "pink": CliTheme(
        name="pink",
        HIGHLIGHT="#FFB3D9",
        BRAND="#D4729A",
        TEXT="#E0E0E0",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#E0B466",
        ERROR="#CF6B63",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "purple": CliTheme(
        name="purple",
        HIGHLIGHT="#C8A8FF",
        BRAND="#9678C0",
        TEXT="#E0E0E0",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#D8B06F",
        ERROR="#CF6B63",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "orange": CliTheme(
        name="orange",
        HIGHLIGHT="#FFC08A",
        BRAND="#D4884A",
        TEXT="#E0E0E0",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#E0B466",
        ERROR="#CF6B63",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "teal": CliTheme(
        name="teal",
        HIGHLIGHT="#8AE2D6",
        BRAND="#5BA89D",
        TEXT="#E0E0E0",
        SECONDARY="#A6A6A6",
        DIM="#6E6E6E",
        WARNING="#CEA25C",
        ERROR="#C45B52",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "lime": CliTheme(
        name="lime",
        HIGHLIGHT="#D4FF7A",
        BRAND="#94C845",
        TEXT="#E0E0E0",
        SECONDARY="#888888",
        DIM="#444444",
        WARNING="#CEA25C",
        ERROR="#C45B52",
        BG="#0A0A0A",
        INPUT_SURFACE="#141414",
    ),
    "nord": CliTheme(
        name="nord",
        HIGHLIGHT="#88C0D0",
        BRAND="#81A1C1",
        TEXT="#E5E9F0",
        SECONDARY="#9AA5B5",
        DIM="#4C566A",
        WARNING="#EBCB8B",
        ERROR="#BF616A",
        BG="#2E3440",
        INPUT_SURFACE="#3B4252",
    ),
    "dracula": CliTheme(
        name="dracula",
        HIGHLIGHT="#BD93F9",
        BRAND="#8BE9FD",
        TEXT="#F8F8F2",
        SECONDARY="#B4B7C5",
        DIM="#6272A4",
        WARNING="#F1FA8C",
        ERROR="#FF5555",
        BG="#282A36",
        INPUT_SURFACE="#303443",
    ),
    "solarized": CliTheme(
        name="solarized",
        HIGHLIGHT="#2AA198",
        BRAND="#268BD2",
        TEXT="#EEE8D5",
        SECONDARY="#93A1A1",
        DIM="#586E75",
        WARNING="#B58900",
        ERROR="#DC322F",
        BG="#002B36",
        INPUT_SURFACE="#073642",
    ),
    "gruvbox": CliTheme(
        name="gruvbox",
        HIGHLIGHT="#FABD2F",
        BRAND="#83A598",
        TEXT="#EBDBB2",
        SECONDARY="#A89984",
        DIM="#665C54",
        WARNING="#FE8019",
        ERROR="#FB4934",
        BG="#282828",
        INPUT_SURFACE="#32302F",
    ),
    "webflux": CliTheme(
        name="webflux",
        HIGHLIGHT="#E23636",
        BRAND="#2B63F5",
        TEXT="#EAF0FF",
        SECONDARY="#9CA8C6",
        DIM="#4B587A",
        WARNING="#FFC857",
        ERROR="#FF4D4D",
        BG="#0D1220",
        INPUT_SURFACE="#151D33",
        SPLASH_GRADIENT_START="#E23636",
        SPLASH_GRADIENT_END="#2B63F5",
    ),
    "sunset": CliTheme(
        name="sunset",
        HIGHLIGHT="#FFB067",
        BRAND="#FF6FA8",
        TEXT="#FFE8D5",
        SECONDARY="#C8A79B",
        DIM="#6C4E4A",
        WARNING="#FFD166",
        ERROR="#FF5A5F",
        BG="#22151A",
        INPUT_SURFACE="#2B1C24",
        SPLASH_GRADIENT_START="#FFB067",
        SPLASH_GRADIENT_END="#FF6FA8",
    ),
}

DEFAULT_THEME_NAME = "blue"


def _fg(rgb: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    stripped = value.lstrip("#")
    return (int(stripped[0:2], 16), int(stripped[2:4], 16), int(stripped[4:6], 16))


class _LazyRichStyle(str):
    """Rich markup colour token that tracks :func:`set_active_theme`.

    Importers can bind ``HIGHLIGHT`` (etc.) at module load; ``str()`` and
    ``f"[{HIGHLIGHT}]"`` resolve against the active palette at render time.
    """

    __slots__ = ("_field", "_bold")

    def __new__(cls, field: str, *, bold: bool = False) -> _LazyRichStyle:
        instance = str.__new__(cls, "")
        object.__setattr__(instance, "_field", field)
        object.__setattr__(instance, "_bold", bold)
        return instance

    def _resolve(self) -> str:
        field = object.__getattribute__(self, "_field")
        bold = object.__getattribute__(self, "_bold")
        value = getattr(_ACTIVE_THEME, field)
        return f"bold {value}" if bold else value

    def __str__(self) -> str:
        return self._resolve()

    def __format__(self, format_spec: str) -> str:
        return format(self._resolve(), format_spec)

    def __bool__(self) -> bool:
        return bool(self._resolve())

    # Hash/compare as the resolved style string. The underlying str value is
    # "" for every token, so without these overrides all tokens collide on
    # the same key in caches keyed by style strings — Rich's lru_cached
    # ``Style.parse`` then renders every ``style=TOKEN`` usage with whichever
    # token happened to be parsed first.
    def __eq__(self, other: object) -> bool:
        return self._resolve() == other

    def __ne__(self, other: object) -> bool:
        return self._resolve() != other

    def __hash__(self) -> int:
        return hash(self._resolve())

    def lstrip(self, chars: str | None = None) -> str:
        resolved = self._resolve()
        return resolved.lstrip() if chars is None else resolved.lstrip(chars)

    def rstrip(self, chars: str | None = None) -> str:
        resolved = self._resolve()
        return resolved.rstrip() if chars is None else resolved.rstrip(chars)

    def strip(self, chars: str | None = None) -> str:
        resolved = self._resolve()
        return resolved.strip() if chars is None else resolved.strip(chars)

    def split(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
        resolved = self._resolve()
        return resolved.split(sep, maxsplit)

    def rsplit(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
        resolved = self._resolve()
        return resolved.rsplit(sep, maxsplit)


def _resolve_theme_name(name: str | None) -> str:
    normalized = (name or DEFAULT_THEME_NAME).strip().lower()
    return normalized if normalized in THEME_REGISTRY else DEFAULT_THEME_NAME


def get_theme(theme_name: str | None) -> CliTheme:
    """Return a registered palette by name; fall back to default."""
    return THEME_REGISTRY[_resolve_theme_name(theme_name)]


def list_theme_names() -> tuple[str, ...]:
    """Return available theme names in display order."""
    return tuple(THEME_REGISTRY.keys())


def get_active_theme() -> CliTheme:
    """Return the currently active palette."""
    return _ACTIVE_THEME


def get_active_theme_name() -> str:
    """Return the currently active palette name."""
    return _ACTIVE_THEME.name


def _apply_theme(theme: CliTheme) -> None:
    global HIGHLIGHT_ANSI, BRAND_ANSI, TEXT_ANSI, SECONDARY_ANSI, DIM_ANSI, BOLD_BRAND_ANSI
    global PROMPT_ACCENT_ANSI, PROMPT_FRAME_ANSI, DIM_COUNTER_ANSI, SURFACE_BG_ANSI
    global INPUT_SURFACE_BG_ANSI, MENU_SELECTION_ROW_ANSI, MARKDOWN_THEME
    global DEVICE_CODE_ANSI

    _highlight_rgb = _parse_hex_color(theme.HIGHLIGHT)
    _brand_rgb = _parse_hex_color(theme.BRAND)
    _text_rgb = _parse_hex_color(theme.TEXT)
    _secondary_rgb = _parse_hex_color(theme.SECONDARY)
    _dim_rgb = _parse_hex_color(theme.DIM)
    _bg_rgb = _parse_hex_color(theme.BG)
    _input_surface_rgb = _parse_hex_color(theme.INPUT_SURFACE)

    HIGHLIGHT_ANSI = _fg(_highlight_rgb)
    BRAND_ANSI = _fg(_brand_rgb)
    TEXT_ANSI = _fg(_text_rgb)
    SECONDARY_ANSI = _fg(_secondary_rgb)
    DIM_ANSI = _fg(_dim_rgb)
    BOLD_BRAND_ANSI = f"\x1b[1m{BRAND_ANSI}"

    PROMPT_ACCENT_ANSI = f"\x1b[1;38;2;{_highlight_rgb[0]};{_highlight_rgb[1]};{_highlight_rgb[2]}m"
    PROMPT_FRAME_ANSI = PROMPT_ACCENT_ANSI
    DEVICE_CODE_ANSI = PROMPT_ACCENT_ANSI
    DIM_COUNTER_ANSI = DIM_ANSI
    SURFACE_BG_ANSI = f"\x1b[48;2;{_bg_rgb[0]};{_bg_rgb[1]};{_bg_rgb[2]}m"
    INPUT_SURFACE_BG_ANSI = (
        f"\x1b[48;2;{_input_surface_rgb[0]};{_input_surface_rgb[1]};{_input_surface_rgb[2]}m"
    )
    MENU_SELECTION_ROW_ANSI = f"{INPUT_SURFACE_BG_ANSI}\x1b[1m{HIGHLIGHT_ANSI}"

    MARKDOWN_THEME = Theme(
        {
            "markdown.code": f"bold {theme.HIGHLIGHT}",
            "markdown.code_block": theme.TEXT,
            "markdown.h1": f"bold {theme.HIGHLIGHT}",
            "markdown.h2": f"bold {theme.BRAND}",
            "markdown.h3": f"bold {theme.BRAND}",
        }
    )


def set_active_theme(theme_name: str | None) -> CliTheme:
    """Activate a palette and refresh all derived style constants."""
    global _ACTIVE_THEME
    _ACTIVE_THEME = get_theme(theme_name)
    _apply_theme(_ACTIVE_THEME)
    return _ACTIVE_THEME


# ── Semantic color tokens (the only permitted colours) ─────────────────────
_ACTIVE_THEME = get_theme(DEFAULT_THEME_NAME)

HIGHLIGHT = _LazyRichStyle("HIGHLIGHT")
BRAND = _LazyRichStyle("BRAND")
TEXT = _LazyRichStyle("TEXT")
SECONDARY = _LazyRichStyle("SECONDARY")
DIM = _LazyRichStyle("DIM")
WARNING = _LazyRichStyle("WARNING")
ERROR = _LazyRichStyle("ERROR")
BG = _LazyRichStyle("BG")
INPUT_SURFACE = _LazyRichStyle("INPUT_SURFACE")

# ── Rich style shorthands (bold variants of the semantic tokens) ──────────

BOLD_HIGHLIGHT = _LazyRichStyle("HIGHLIGHT", bold=True)
BOLD_BRAND = _LazyRichStyle("BRAND", bold=True)
BOLD_TEXT = _LazyRichStyle("TEXT", bold=True)
BOLD_WARNING = _LazyRichStyle("WARNING", bold=True)
BOLD_ERROR = _LazyRichStyle("ERROR", bold=True)

# GitHub/device-flow one-time codes should be easy to spot and transcribe.
DEVICE_CODE = BOLD_HIGHLIGHT

# Distinct accent for incoming alerts (visually distinct from BOLD_BRAND used for assistant)
INCOMING_ALERT_ACCENT = BOLD_WARNING

__all__ = [
    "ANSI_BOLD",
    "ANSI_DIM",
    "ANSI_RESET",
    "BG",
    "BOLD_BRAND",
    "BOLD_BRAND_ANSI",
    "BOLD_ERROR",
    "BOLD_HIGHLIGHT",
    "BOLD_TEXT",
    "BOLD_WARNING",
    "BRAND",
    "BRAND_ANSI",
    "DEVICE_CODE",
    "DEVICE_CODE_ANSI",
    "DIM",
    "DIM_ANSI",
    "DIM_COUNTER_ANSI",
    "ERROR",
    "GLYPH_ACTIVE",
    "GLYPH_BULLET",
    "GLYPH_ERROR",
    "GLYPH_PROMPT",
    "GLYPH_SUCCESS",
    "GLYPH_WARNING",
    "HIGHLIGHT",
    "HIGHLIGHT_ANSI",
    "INCOMING_ALERT_ACCENT",
    "INPUT_SURFACE",
    "INPUT_SURFACE_BG_ANSI",
    "MARKDOWN_THEME",
    "MENU_SELECTION_ROW_ANSI",
    "PROMPT_ACCENT_ANSI",
    "PROMPT_FRAME_ANSI",
    "SECONDARY",
    "SECONDARY_ANSI",
    "SURFACE_BG_ANSI",
    "TEXT",
    "TEXT_ANSI",
    "WARNING",
]

# ── Semantic glyphs ────────────────────────────────────────────────────────

GLYPH_SUCCESS = "✓"
GLYPH_WARNING = "⚠"
GLYPH_ERROR = "✗"
GLYPH_PROMPT = "◆"
GLYPH_ACTIVE = "◉"
GLYPH_BULLET = "·"

# ── ANSI escape sequences for prompt_toolkit (bypasses Rich markup) ────────
# This module is the only place in the codebase where raw ANSI escapes are
# permitted. Every truecolour value below corresponds to one of the eight
# semantic tokens above.

# Placeholders — populated by :func:`set_active_theme` (ANSI + Markdown only).
HIGHLIGHT_ANSI = ""
BRAND_ANSI = ""
TEXT_ANSI = ""
SECONDARY_ANSI = ""
DIM_ANSI = ""
BOLD_BRAND_ANSI = ""
DEVICE_CODE_ANSI = ""

ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"

PROMPT_ACCENT_ANSI = ""
PROMPT_FRAME_ANSI = ""
DIM_COUNTER_ANSI = ""
SURFACE_BG_ANSI = ""
INPUT_SURFACE_BG_ANSI = ""
MENU_SELECTION_ROW_ANSI = ""

MARKDOWN_THEME = Theme({})

# Ensure ANSI/Markdown derived constants match the default active theme.
set_active_theme(DEFAULT_THEME_NAME)
