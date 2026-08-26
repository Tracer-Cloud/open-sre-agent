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
