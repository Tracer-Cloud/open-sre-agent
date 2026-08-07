"""Tests for the /choose slash command (pending ask_user_choice menu)."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

import surfaces.interactive_shell.command_registry.choice_prompt as choice_prompt
from core.agent_harness.session.pending_choice import PendingUserChoice
from surfaces.interactive_shell.session import Session

_CHOICE = PendingUserChoice(
    title="How should I handle the uncommitted changes?",
    options=(
        "Stash the changes (recommended – quick & safe)",
        "Commit the changes",
        "Use a separate git worktree",
    ),
)


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def _handler(session: Session, console: Console) -> bool:
    return choice_prompt._cmd_choose(session, console, [])


def test_selection_is_auto_submitted_as_next_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.pending_user_choice = _CHOICE
    console, _buf = _console()

    def _pick_second(**kwargs: object) -> str:
        assert kwargs["title"] == _CHOICE.title
        return "Commit the changes"

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _pick_second)

    assert _handler(session, console) is True
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default == "Commit the changes"
    assert session.terminal.pending_prompt_autosubmit is True


def test_cancelled_menu_leaves_prompt_free(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    session.pending_user_choice = _CHOICE
    console, buf = _console()

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: None)

    assert _handler(session, console) is True
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False
    assert "cancelled" in buf.getvalue().lower()


def test_no_pending_choice_prints_notice() -> None:
    session = Session()
    console, buf = _console()

    assert _handler(session, console) is True
    assert "no selection menu is pending" in buf.getvalue().lower()


def test_non_tty_prints_options_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    session.pending_user_choice = _CHOICE
    console, buf = _console()

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: False)

    assert _handler(session, console) is True
    output = buf.getvalue()
    assert _CHOICE.title in output
    for option in _CHOICE.options:
        assert option in output
    assert session.terminal.pending_prompt_default is None


def test_choose_is_registered_with_exclusive_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    import surfaces.interactive_shell.runtime.utils.input_policy as input_policy
    from surfaces.interactive_shell.command_registry import SLASH_COMMANDS

    assert "/choose" in SLASH_COMMANDS

    # turn_needs_exclusive_stdin consults the module-level TTY check; force it
    # interactive so the registration (not the test environment) is asserted.
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    assert input_policy.turn_needs_exclusive_stdin("/choose", Session()) is True
