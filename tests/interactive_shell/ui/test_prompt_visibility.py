"""Typing box must hide while options / confirmation own the keyboard."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from core.agent_harness.spi.handoff import AskUserQuestion
from core.agent_harness.spi.session_state import PendingUserChoice
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState, TurnPhase
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.prompt_visibility import (
    clear_live_prompt_paint,
    typing_box_hidden,
)
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region


def _plain(ansi: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07", "", ansi)


def test_typing_box_hidden_while_awaiting_confirmation() -> None:
    session = Session()
    confirming = ReplState()
    confirming.phase = TurnPhase.AWAITING_CONFIRMATION
    confirming.confirm_prompt_text = "Yes, allow? [Y/n] "

    assert typing_box_hidden(session, confirming) is True
    rendered = _plain(render_prompt_region(session, confirming, SpinnerState()).value)
    assert "❯" not in rendered
    assert "Yes, allow? [Y/n]" in rendered


def test_typing_box_hidden_while_ask_user_options_pending() -> None:
    session = Session()
    session.pending_user_choice = PendingUserChoice(
        title="Ask User",
        options=("A", "B"),
        questions=(
            AskUserQuestion(label="Shape", title="Which shape?", options=("A", "B")),
            AskUserQuestion(label="Scope", title="Which scope?", options=("C", "D")),
        ),
    )

    assert typing_box_hidden(session, ReplState()) is True
    rendered = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert "❯" not in rendered


def test_typing_box_hidden_during_plan_with_ask_user_pending() -> None:
    """Plan overlay may stay; the free-text composer must not compete with Ask User."""
    from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

    session = Session()
    session.task_plan = TaskPlan(
        steps=(
            PlanStep(step="Clarify blockers", status=PlanStepStatus.IN_PROGRESS),
            PlanStep(step="Write diagnosis", status=PlanStepStatus.PENDING),
        )
    )
    session.pending_user_choice = PendingUserChoice(
        title="Ask User",
        options=("A", "B"),
        questions=(
            AskUserQuestion(label="Shape", title="Which shape?", options=("A", "B")),
            AskUserQuestion(label="Scope", title="Which scope?", options=("C", "D")),
        ),
    )

    assert typing_box_hidden(session, ReplState()) is True
    rendered = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert "❯" not in rendered
    assert "Clarify blockers" in rendered or "Plan" in rendered


def test_typing_box_hidden_while_exclusive_stdin_menu_active() -> None:
    session = Session()
    session.terminal.exclusive_stdin_active = True
    assert typing_box_hidden(session, ReplState()) is True
    rendered = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert "❯" not in rendered


def test_idle_prompt_still_shows_typing_box() -> None:
    session = Session()
    assert typing_box_hidden(session, ReplState()) is False
    rendered = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert "❯" in rendered


def test_prompt_region_height_stable_when_typing_box_hides() -> None:
    session = Session()
    spinner = SpinnerState()
    idle_rows = render_prompt_region(session, ReplState(), spinner).value.count("\n")

    confirming = ReplState()
    confirming.phase = TurnPhase.AWAITING_CONFIRMATION
    confirming.confirm_prompt_text = "Proceed? [Y/n]"
    confirm_rows = render_prompt_region(session, confirming, spinner).value.count("\n")

    assert idle_rows == confirm_rows


def test_clear_live_prompt_paint_erases_only_the_app_region() -> None:
    # ``erase`` wipes the app's own rows (leaving the transcript) so the menu
    # draws inline like Droid; a full ``clear`` would read as a new window.
    session = Session()
    renderer = MagicMock()
    app = MagicMock()
    app.renderer = renderer
    app.is_running = True
    session.terminal.prompt_app = app

    clear_live_prompt_paint(session)

    renderer.erase.assert_called_once()
    renderer.clear.assert_not_called()
    app.invalidate.assert_called_once()
