"""Tests for the responsive Braille-logo splash layout."""

from __future__ import annotations

import io
import re

from rich.cells import cell_len
from rich.console import Console

from surfaces.interactive_shell.ui.banner import banner as banner_module
from surfaces.interactive_shell.ui.banner import splash_layout

_BRAILLE = re.compile(r"[⠀-⣿]")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Rows unique to each variant (the small logo shares no full row with the large).
_LARGE_MARKER = splash_layout.BRAILLE_LOGO_LARGE[1]
_SMALL_MARKER = splash_layout.BRAILLE_LOGO_SMALL[1]


def _render_splash(width: int) -> Console:
    console = Console(
        file=io.StringIO(),
        record=True,
        width=width,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    banner_module.render_splash(console, first_run=False)
    return console


def test_mode_selection_thresholds() -> None:
    assert splash_layout.select_splash_mode(120) == "wide"
    assert splash_layout.select_splash_mode(90) == "wide"
    assert splash_layout.select_splash_mode(89) == "medium"
    assert splash_layout.select_splash_mode(80) == "medium"
    assert splash_layout.select_splash_mode(60) == "medium"
    assert splash_layout.select_splash_mode(59) == "narrow"
    assert splash_layout.select_splash_mode(50) == "narrow"


def test_wide_120_shows_large_logo_beside_content() -> None:
    plain = _render_splash(120).export_text(styles=False)
    assert _LARGE_MARKER in plain
    assert _SMALL_MARKER not in plain
    assert "opensre" in plain
    assert "open-source SRE agent" in plain
    # Logo and content share rows (side by side, not stacked).
    assert any(_BRAILLE.search(line) and "opensre" in line for line in plain.splitlines())


def test_medium_80_shows_small_logo_with_condensed_content() -> None:
    plain = _render_splash(80).export_text(styles=False)
    assert _SMALL_MARKER in plain
    assert _LARGE_MARKER not in plain
    assert "opensre" in plain
    assert "open-source SRE agent" in plain


def test_narrow_50_is_text_only_stacked() -> None:
    plain = _render_splash(50).export_text(styles=False)
    assert _BRAILLE.search(plain) is None
    assert "█" not in plain
    assert "opensre" in plain
    assert "open-source SRE agent" in plain


def test_no_rendered_line_exceeds_terminal_width() -> None:
    for width in (120, 90, 89, 80, 60, 59, 50):
        plain = _render_splash(width).export_text(styles=False)
        for line in plain.splitlines():
            assert cell_len(line.rstrip()) <= width, f"line wider than {width} cols: {line!r}"


def test_ansi_styling_does_not_affect_layout_width() -> None:
    for width in (120, 80, 50):
        console = _render_splash(width)
        raw = console.file.getvalue()  # type: ignore[union-attr]
        assert "\x1b[" in raw  # styling was actually emitted
        for line in _ANSI.sub("", raw).splitlines():
            assert cell_len(line.rstrip()) <= width


def test_logo_column_stays_aligned() -> None:
    for width, logo in (
        (120, splash_layout.BRAILLE_LOGO_LARGE),
        (80, splash_layout.BRAILLE_LOGO_SMALL),
    ):
        plain = _render_splash(width).export_text(styles=False)
        braille_lines = [line for line in plain.splitlines() if _BRAILLE.search(line)]
        assert len(braille_lines) == len(logo)
        # Every logo row starts at the same two-space indent column.
        starts = {_BRAILLE.search(line).start() for line in braille_lines}  # type: ignore[union-attr]
        assert starts == {2}
        assert all(line[:2] == "  " for line in braille_lines)


def test_ready_box_shortcut_lines_stay_within_width() -> None:
    # The welcome panel below the splash carries the /help, /doctor shortcuts;
    # its box must stay aligned and never exceed the terminal width.
    for width in (120, 80, 50):
        console = Console(
            file=io.StringIO(),
            record=True,
            width=width,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
            legacy_windows=False,
        )
        banner_module.render_ready_box(console)
        plain = console.export_text(styles=False)
        box_lines = [line for line in plain.splitlines() if line.startswith(("╭", "│", "╰"))]
        assert box_lines
        assert {cell_len(line.rstrip()) for line in box_lines} == {
            max(cell_len(line.rstrip()) for line in box_lines)
        }
        assert all(cell_len(line.rstrip()) <= width for line in plain.splitlines())
