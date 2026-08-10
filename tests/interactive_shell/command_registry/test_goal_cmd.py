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
