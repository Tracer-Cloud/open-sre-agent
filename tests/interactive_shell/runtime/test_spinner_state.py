"""Tests for the inline turn spinner in runtime.core.state."""

from __future__ import annotations

import re
import time

from surfaces.interactive_shell.runtime.core.state import SpinnerState

_GLYPHS = SpinnerState._SPINNER_FRAMES


def _glyph(spinner: SpinnerState) -> str:
    rendered = spinner.inline_spinner_ansi()
    match = re.search("|".join(map(re.escape, _GLYPHS)), rendered)
    assert match is not None, f"no spinner glyph in {rendered!r}"
    return match.group(0)


def test_spinner_frame_is_a_function_of_elapsed_time_not_call_count() -> None:
    """Regression: the glyph must animate no matter how often it is rendered.

    prompt_toolkit evaluates the prompt message callable several times per
    render pass. A per-call frame counter advanced exactly one full cycle per
    visible render (10 evals x 10 frames), so the on-screen glyph never
    changed. Deriving the frame from elapsed time makes repeated evaluations
    idempotent and guarantees the animation advances between renders.
    """
    spinner = SpinnerState()
    spinner.start()

    # Repeated evaluations inside one render pass: same frame every time.
    first = _glyph(spinner)
    assert all(_glyph(spinner) == first for _ in range(10))

    # A frame interval later the glyph must have advanced.
    spinner.started_at -= SpinnerState._FRAME_INTERVAL_SECONDS * 1.01
    assert _glyph(spinner) != first

    # Over a full cycle of elapsed time, every frame is visited in order.
    spinner.start()
    seen = []
    for step in range(len(_GLYPHS)):
        spinner.started_at = time.monotonic() - step * SpinnerState._FRAME_INTERVAL_SECONDS * 1.001
        seen.append(_glyph(spinner))
    assert seen == list(_GLYPHS)


def test_spinner_renders_elapsed_seconds_and_cancel_hint() -> None:
    spinner = SpinnerState()
    spinner.start()
    spinner.started_at = time.monotonic() - 8.2

    rendered = spinner.inline_spinner_ansi()

    assert "[ 8s]" in rendered
    assert "(Press ESC to stop)" in rendered
    assert SpinnerState.EXECUTING_PHASE in rendered


def test_spinner_invoking_tools_phase_matches_factory_copy() -> None:
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)

    rendered = spinner.inline_spinner_ansi()

    assert SpinnerState.INVOKING_TOOLS_PHASE in rendered
    assert "(Press ESC to stop)" in rendered


def test_invoking_tools_uses_brand_accent_not_highlight() -> None:
    """Tool phase is brand-colored so it contrasts with Thinking (highlight)."""
    from infrastructure.terminal.theme import set_active_theme

    set_active_theme("blue")
    spinner = SpinnerState()
    spinner.start()
    thinking = spinner.inline_spinner_ansi()
    assert "168;212;255" in thinking  # highlight
    assert "111;165;216" not in thinking.split("(Press ESC")[0]

    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)
    invoking = spinner.inline_spinner_ansi()
    assert SpinnerState.INVOKING_TOOLS_PHASE in invoking
    assert "111;165;216" in invoking  # brand
    # Lead+label must not stay on highlight once tools are invoking.
    lead = invoking.split("(Press ESC")[0]
    assert "168;212;255" not in lead


def test_spinner_empty_when_not_streaming() -> None:
    spinner = SpinnerState()
    assert spinner.inline_spinner_ansi() == ""
    spinner.start()
    spinner.stop()
    assert spinner.inline_spinner_ansi() == ""


def test_inline_spinner_clips_a_long_phase_to_one_prompt_row() -> None:
    """A long phase label must not soft-wrap past the one reserved prompt row."""
    from surfaces.shared.terminal.prompt_layout import prompt_line_width

    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase("X" * 400)
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())

    assert len(rendered) <= prompt_line_width()
