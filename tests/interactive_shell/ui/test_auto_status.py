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


def test_auto_status_recedes_while_the_spinner_owns_the_accent() -> None:
    """Busy turns keep Auto on the page but DIM — one gold line, not two."""
    import re

    import infrastructure.terminal.theme as ui_theme
    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
    from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

    ui_theme.set_active_theme("amber")
    session = Session()
    session.terminal.auto_level = AutoLevel.HIGH
    loud = auto_status_ansi(session, quiet=False)
    quiet = auto_status_ansi(session, quiet=True)
    assert ui_theme.BOLD_REPLY_MARKER_ANSI in loud
    assert ui_theme.BOLD_REPLY_MARKER_ANSI not in quiet
    assert ui_theme.DIM_ANSI in quiet
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.THINKING_PHASE)
    rendered = render_prompt_region(session, ReplState(), spinner).value
    plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07", "", rendered)
    assert "Thinking" in plain
    assert "Auto (High)" in plain
    assert plain.index("Thinking") < plain.index("Auto (High)")
    assert ui_theme.DIM_ANSI + "Auto (High)" in rendered


def test_high_auto_omits_the_default_caption() -> None:
    """High is the default — do not print ``all actions allowed`` on every frame."""
    import re

    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
    from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

    session = Session()
    session.terminal.auto_level = AutoLevel.HIGH
    plain = re.sub(
        r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07",
        "",
        render_prompt_region(session, ReplState(), SpinnerState()).value,
    )
    assert "Auto (High)" in plain
    assert "all actions allowed" not in plain
    session.terminal.auto_level = AutoLevel.MED
    med = re.sub(
        r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07",
        "",
        render_prompt_region(session, ReplState(), SpinnerState()).value,
    )
    assert "allow reversible commands" in med


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


def test_confirmation_region_height_is_constant_while_confirming() -> None:
    """The Yes/No block is a taller modal than the idle prompt, but its own
    height must not change as the arrow selection moves between the options."""
    from surfaces.interactive_shell.runtime.core.state import (
        ReplState,
        SpinnerState,
        TurnPhase,
    )
    from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

    session = Session()
    spinner = SpinnerState()

    def _confirm_rows(selected: int) -> int:
        state = ReplState()
        state.phase = TurnPhase.AWAITING_CONFIRMATION
        state.confirm_prompt_text = "Approve this action?"
        state.confirm_selected = selected
        return render_prompt_region(session, state, spinner).value.count("\n")

    assert _confirm_rows(0) == _confirm_rows(1)
