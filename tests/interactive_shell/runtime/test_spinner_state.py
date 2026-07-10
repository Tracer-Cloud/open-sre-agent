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
    spinner.started_at -= SpinnerState._FRAME_INTERVAL_S * 1.01
    assert _glyph(spinner) != first

    # Over a full cycle of elapsed time, every frame is visited in order.
    spinner.start()
    seen = []
    for step in range(len(_GLYPHS)):
        spinner.started_at = time.monotonic() - step * SpinnerState._FRAME_INTERVAL_S * 1.001
        seen.append(_glyph(spinner))
    assert seen == list(_GLYPHS)


def test_spinner_renders_elapsed_seconds_and_cancel_hint() -> None:
    spinner = SpinnerState()
    spinner.start()
    spinner.started_at = time.monotonic() - 8.2

    rendered = spinner.inline_spinner_ansi()

    assert "(8s)" in rendered
    assert "esc to cancel" in rendered


def test_spinner_empty_when_not_streaming() -> None:
    spinner = SpinnerState()
    assert spinner.inline_spinner_ansi() == ""
    spinner.start()
    spinner.stop()
    assert spinner.inline_spinner_ansi() == ""
