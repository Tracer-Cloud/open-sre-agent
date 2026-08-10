"""Slash sugar for the outer SessionGoal API: /goal show|set|clear."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape as _rich_escape

from core.agent_harness.session.session_goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
    clear_session_goal,
    format_session_goal_checklist,
    session_goal_is_active,
)
from platform.terminal.theme import DIM, ERROR, HIGHLIGHT
from surfaces.interactive_shell.runtime import Session


def _show(session: Session, console: Console) -> bool:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_active(session):
        console.print(
            f"[{DIM}]no active session goal.[/] "
            f"Set one with [{HIGHLIGHT}]/goal set <condition>[/] "
            f"or let the action agent attach ``session_goal:…``."
        )
        return True
    console.print(f"[{HIGHLIGHT}]session goal[/] ({_rich_escape(goal.status)})")
    console.print(f"  condition: {_rich_escape(goal.condition)}")
    console.print(
        f"  turns: {goal.turns_used}/{goal.max_outer_turns}"
        + (f"  steps: {goal.step_count}" if goal.step_count is not None else "")
    )
    checklist = format_session_goal_checklist(goal)
    if checklist:
        console.print(checklist)
    return True


def _set(session: Session, console: Console, args: list[str]) -> bool:
    max_turns = 5
    rest = list(args)
    while rest and rest[0].startswith("--"):
        flag = rest.pop(0)
        if flag in {"--max-turns", "--max_turns"} and rest:
            try:
                max_turns = max(1, int(rest.pop(0)))
            except ValueError:
                console.print(f"[{ERROR}]usage:[/] /goal set [--max-turns N] <condition>")
                return True
        else:
            console.print(f"[{ERROR}]unknown flag:[/] {_rich_escape(flag)}")
            return True
    condition = " ".join(rest).strip()
    if not condition:
        console.print(f"[{ERROR}]usage:[/] /goal set [--max-turns N] <condition>")
        return True
    goal = SessionGoal(
        condition=condition,
        max_outer_turns=max_turns,
        status=SessionGoalStatus.ACTIVE,
    )
    attach_session_goal(session, goal)
    console.print(
        f"[{HIGHLIGHT}]session goal set[/] "
        f"(max_turns={max_turns}): {_rich_escape(condition)}"
    )
    return True


def _clear(session: Session, console: Console) -> bool:
    if getattr(session, "session_goal", None) is None:
        console.print(f"[{DIM}]no session goal to clear.[/]")
        return True
    clear_session_goal(session)
    console.print(f"[{HIGHLIGHT}]session goal cleared.[/]")
    return True


def _cmd_goal(session: Session, console: Console, args: list[str]) -> bool:
    if not args:
        return _show(session, console)
    sub = args[0].lower()
    if sub in {"show", "status"}:
        return _show(session, console)
    if sub == "set":
        return _set(session, console, args[1:])
    if sub in {"clear", "unset"}:
        return _clear(session, console)
    console.print(f"[{ERROR}]usage:[/] /goal [show|set|clear]")
    return True
