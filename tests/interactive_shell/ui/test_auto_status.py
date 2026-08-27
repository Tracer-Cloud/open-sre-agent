"""Auto-status ANSI line: model labels must not inject terminal controls."""

from __future__ import annotations

from config.constants.repl_autonomy import AutoLevel
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.auto_status import auto_status_ansi


def test_model_label_strips_terminal_controls(monkeypatch) -> None:
    """A configured model id with ESC/OSC/BEL must not reach the ANSI status line."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.auto_status.detect_provider_model",
        lambda: ("openai", "gpt-4\x1b]0;pwn\x07o\x1b[2J\x1b[1;1H"),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.auto_status.prompt_line_width",
        lambda: 120,
    )
    session = Session()
    session.terminal.auto_level = AutoLevel.MED

    rendered = auto_status_ansi(session)

    assert "\x1b]" not in rendered
    assert "\x07" not in rendered
    assert "\x1b[2J" not in rendered
    assert "\x1b[1;1H" not in rendered
    assert "gpt-4" in rendered
    assert "Auto (Med)" in rendered


def test_all_control_model_falls_back_to_left_only(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.auto_status.detect_provider_model",
        lambda: ("openai", "\x1b\x07\x9b"),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.auto_status.prompt_line_width",
        lambda: 80,
    )

    rendered = auto_status_ansi(Session())

    assert "\x1b[2J" not in rendered
    assert "\x07" not in rendered
    assert "Auto (" in rendered


def test_render_prompt_region_shows_the_auto_status_line() -> None:
    """The live prompt composition must reach ``auto_status_ansi`` so the level shows."""
    import re

    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
    from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

    session = Session()
    session.terminal.auto_level = AutoLevel.MED

    rendered = render_prompt_region(session, ReplState(), SpinnerState()).value
    plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07", "", rendered)

    assert "Auto (Med)" in plain


def test_prompt_region_height_is_constant_across_confirmation() -> None:
    """Entering/leaving confirmation must not change the region's row count."""
    from surfaces.interactive_shell.runtime.core.state import (
        ReplState,
        SpinnerState,
        TurnPhase,
    )
    from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

    session = Session()
    spinner = SpinnerState()

    normal_rows = render_prompt_region(session, ReplState(), spinner).value.count("\n")

    confirming = ReplState()
    confirming.phase = TurnPhase.AWAITING_CONFIRMATION
    confirming.confirm_prompt_text = "Proceed? [Y/n]"
    confirm_rows = render_prompt_region(session, confirming, spinner).value.count("\n")

    assert normal_rows == confirm_rows
