"""/goal slash sugar over SessionGoal."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from core.agent_harness.session.session_goal import session_goal_is_active
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
    """Claude-shaped: setting a goal starts work without a separate prompt."""
    session = Session()
    console, _buf = _console()

    assert _cmd_goal(session, console, ["set", "all auth tests pass"])
    assert session.terminal.pending_prompt_default == "all auth tests pass"
    assert session.terminal.pending_prompt_autosubmit is True


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
    """Claude-shaped ``/goal <condition>`` (no ``set``) attaches and autosubmits."""
    session = Session()
    console, _buf = _console()

    assert _cmd_goal(session, console, ["all", "tests", "in", "test/auth", "pass"])
    assert session_goal_is_active(session)
    assert session.session_goal is not None
    assert session.session_goal.condition == "all tests in test/auth pass"
    assert session.terminal.pending_prompt_default == "all tests in test/auth pass"
    assert session.terminal.pending_prompt_autosubmit is True
