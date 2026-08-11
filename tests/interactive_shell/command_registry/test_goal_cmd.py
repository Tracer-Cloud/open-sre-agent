"""/goal slash sugar over SessionGoal."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from core.agent_harness.session_goal.goal import session_goal_is_active
from surfaces.interactive_shell.command_registry.session_cmds.goal import _cmd_goal
from surfaces.interactive_shell.session import Session


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, width=120), buf


def test_goal_set_show_and_clear() -> None:
    session = Session()
    console, buf = _console()

    assert _cmd_goal(session, console, ["set", "--max-turns", "3", "finish the checklist"])
    assert session_goal_is_active(session)
    assert session.session_goal is not None
    assert session.session_goal.max_outer_turns == 3
    assert "finish the checklist" in session.session_goal.condition

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, [])
    out = buf.getvalue()
    assert "finish the checklist" in out

    assert _cmd_goal(session, console, ["clear"])
    assert not session_goal_is_active(session)


def test_goal_set_queues_condition_as_immediate_turn() -> None:
    """Setting a goal starts work without a separate prompt."""
    session = Session()
    console, buf = _console()

    assert _cmd_goal(session, console, ["set", "--max-turns", "3", "all auth tests pass"])
    assert session.terminal.pending_prompt_default == "all auth tests pass"
    assert session.terminal.pending_prompt_autosubmit is True
    assert session.session_goal is not None
    assert session.session_goal.host_owned is True
    assert session.session_goal.max_outer_turns == 3
    assert session.session_goal.started_at is not None
    out = buf.getvalue()
    assert "◎ /goal active" in out
    assert "—" not in out.split("◎ /goal active", 1)[1].split("\n", 1)[0]
    assert "+0 tokens" in out


def test_goal_show_prints_active_duration_and_tokens() -> None:
    session = Session()
    session.tokens.record(input_tokens=100, output_tokens=50)
    console, buf = _console()

    assert _cmd_goal(session, console, ["set", "--max-turns", "4", "ship the fix"])
    session.tokens.record(input_tokens=200, output_tokens=100)

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, ["show"])
    out = buf.getvalue()
    assert "◎ /goal active" in out
    assert "ship the fix" in out
    assert "tokens" in out.lower()
    assert "turn" in out.lower()


def test_bare_goal_condition_is_set_and_starts_turn() -> None:
    """Bare ``/goal <condition>`` (no ``set``) attaches and autosubmits."""
    session = Session()
    console, _buf = _console()

    assert _cmd_goal(session, console, ["all", "tests", "in", "test/auth", "pass"])
    assert session_goal_is_active(session)
    assert session.session_goal is not None
    assert session.session_goal.condition == "all tests in test/auth pass"
    assert session.terminal.pending_prompt_default == "all tests in test/auth pass"
    assert session.terminal.pending_prompt_autosubmit is True


def test_goal_pause_resume_and_edit() -> None:
    from core.agent_harness.session_goal.goal import (
        session_goal_is_attached,
        session_goal_is_paused,
    )

    session = Session()
    console, buf = _console()

    assert _cmd_goal(session, console, ["set", "--max-turns", "4", "ship the fix"])
    assert session_goal_is_active(session)
    turns_before = session.session_goal.turns_used if session.session_goal else -1

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, ["pause"])
    assert session_goal_is_paused(session)
    assert session_goal_is_attached(session)
    assert not session_goal_is_active(session)
    assert "◎ /goal paused" in buf.getvalue()
    assert session.session_goal is not None
    assert session.session_goal.turns_used == turns_before

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, ["edit", "ship", "the", "fix", "and", "tests"])
    assert session.session_goal is not None
    assert session.session_goal.condition == "ship the fix and tests"
    assert session_goal_is_paused(session)
    assert session.terminal.pending_prompt_autosubmit is not True

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, ["resume"])
    assert session_goal_is_active(session)
    assert "◎ /goal active" in buf.getvalue()
    assert session.terminal.pending_prompt_default == "ship the fix and tests"
    assert session.terminal.pending_prompt_autosubmit is True


def test_goal_edit_while_active_queues_new_condition() -> None:
    session = Session()
    console, _buf = _console()

    assert _cmd_goal(session, console, ["set", "old condition"])
    session.terminal.pending_prompt_default = None
    session.terminal.pending_prompt_autosubmit = False
    assert _cmd_goal(session, console, ["edit", "new", "condition"])
    assert session.session_goal is not None
    assert session.session_goal.condition == "new condition"
    assert session_goal_is_active(session)
    assert session.terminal.pending_prompt_default == "new condition"
    assert session.terminal.pending_prompt_autosubmit is True


def test_goal_show_includes_paused() -> None:
    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        SessionGoalStatus,
        attach_session_goal,
    )

    session = Session()
    console, buf = _console()
    attach_session_goal(
        session,
        SessionGoal(
            condition="paused work",
            max_outer_turns=3,
            status=SessionGoalStatus.PAUSED,
            host_owned=True,
        ),
    )
    assert _cmd_goal(session, console, ["show"])
    assert "◎ /goal paused" in buf.getvalue()
    assert "paused work" in buf.getvalue()
