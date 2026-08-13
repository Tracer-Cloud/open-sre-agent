"""/goal slash command: show|set|pause|resume|edit|clear.

``/goal set`` attaches a completion condition and immediately queues that
condition as the next turn (autosubmit). Status shows the paint progress line
(``SESSION_GOAL_PAINT_MARK`` + ``/goal active``) with duration, turn budget,
and token delta. User-facing copy never says ``SessionGoal`` — only ``/goal``.
"""

from __future__ import annotations

from dataclasses import replace

from rich.console import Console
from rich.markup import escape as _rich_escape

from core.agent_harness.session import SessionManager
from core.agent_harness.session.terminal_access import (
    clear_pending_autosubmit,
    set_auto_command,
)
from core.agent_harness.session_goal.goal import (
    MAX_GOAL_CONDITION_CHARS,
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
    clear_session_goal,
    session_goal_is_active,
    session_goal_is_attached,
    session_goal_is_paused,
)
from core.agent_harness.session_goal.progress import format_session_goal_progress
from platform.common.evidence_compaction import truncate_message
from platform.terminal.theme import DIM, ERROR, HIGHLIGHT
from surfaces.interactive_shell.runtime import Session

_GOAL_SUBCOMMANDS = frozenset(
    {"show", "status", "set", "clear", "unset", "pause", "resume", "edit"}
)
_USAGE = "/goal [show|set|pause|resume|edit|clear]  or  /goal <condition>"
_HELP_FOOTER = "/goal · pause · resume · edit · clear"


def _persist_goal_state(session: Session) -> None:
    """Flush so gateway ``resolve`` (fresh SessionCore per turn) sees mutations.

    Interactive shell keeps one in-memory session, but chat transports rebuild
    from the transcript each inbound message — pause/resume/edit/clear/set must
    land as ``session_goal_state`` or the next turn restores the prior snapshot.
    """
    SessionManager.for_session(session).flush(session)


def _show(session: Session, console: Console) -> bool:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_attached(session):
        console.print(
            f"[{DIM}]no active goal.[/] Set one with [{HIGHLIGHT}]/goal set <condition>[/]."
        )
        return True
    console.print(format_session_goal_progress(goal, session=session), markup=False)
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
        host_owned=True,
    )
    goal = attach_session_goal(session, goal)
    # Setting starts work immediately: queue the condition on the REPL loop
    # (no-op terminal → turn-outcome hint on headless SessionCore).
    set_auto_command(session, condition)
    _persist_goal_state(session)
    console.print(format_session_goal_progress(goal, session=session), markup=False)
    console.print(
        f"[{DIM}]→ next: condition runs as its own prompt turn "
        f"(look for [/][{HIGHLIGHT}][N] ❯[/][{DIM}] below). [/]"
        f"[{DIM}]{_HELP_FOOTER}[/]"
    )
    return True


def _pause(session: Session, console: Console) -> bool:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_active(session):
        if isinstance(goal, SessionGoal) and session_goal_is_paused(session):
            console.print(format_session_goal_progress(goal, session=session), markup=False)
            console.print(f"[{DIM}]already paused.[/]")
            return True
        console.print(f"[{DIM}]no active goal to pause.[/]")
        return True
    paused = goal.with_status(SessionGoalStatus.PAUSED).with_reason(
        SessionGoalReason.PAUSED_BY_USER
    )
    paused = attach_session_goal(session, paused)
    # Drop any queued autosubmit so pause actually stops the next turn.
    # Gateway SessionCore has no terminal facet — must not AttributeError
    # before flush (or the next inbound message restores ACTIVE).
    clear_pending_autosubmit(session)
    _persist_goal_state(session)
    console.print(format_session_goal_progress(paused, session=session), markup=False)
    console.print(f"[{DIM}]paused. {_HELP_FOOTER.replace('pause · ', '')}[/]")
    return True


def _resume(session: Session, console: Console) -> bool:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_paused(session):
        if isinstance(goal, SessionGoal) and session_goal_is_active(session):
            console.print(format_session_goal_progress(goal, session=session), markup=False)
            console.print(f"[{DIM}]already active.[/]")
            return True
        console.print(f"[{DIM}]no paused goal to resume.[/]")
        return True
    resumed = goal.with_status(SessionGoalStatus.ACTIVE).with_reason(
        SessionGoalReason.WAITING_HOST_SIGNAL
        if goal.host_owned
        else SessionGoalReason.WAITING_TOOL_EVIDENCE
    )
    resumed = attach_session_goal(session, resumed)
    set_auto_command(session, resumed.condition)
    _persist_goal_state(session)
    console.print(format_session_goal_progress(resumed, session=session), markup=False)
    console.print(
        f"[{DIM}]→ next: condition runs as its own prompt turn "
        f"(look for [/][{HIGHLIGHT}][N] ❯[/][{DIM}] below). [/]"
        f"[{DIM}]{_HELP_FOOTER}[/]"
    )
    return True


def _edit(session: Session, console: Console, args: list[str]) -> bool:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_attached(session):
        console.print(
            f"[{DIM}]no goal to edit. Set one with [/][{HIGHLIGHT}]/goal set <condition>[/]."
        )
        return True
    condition = " ".join(args).strip()
    if not condition:
        console.print(f"[{ERROR}]usage:[/] /goal edit <condition>")
        return True
    condition = truncate_message(condition, MAX_GOAL_CONDITION_CHARS)
    edited = replace(goal, condition=condition)
    edited = attach_session_goal(session, edited)
    if session_goal_is_active(session):
        set_auto_command(session, condition)
    _persist_goal_state(session)
    console.print(format_session_goal_progress(edited, session=session), markup=False)
    if session_goal_is_active(session):
        console.print(
            f"[{DIM}]→ next: updated condition runs as its own prompt turn "
            f"(look for [/][{HIGHLIGHT}][N] ❯[/][{DIM}] below). [/]"
            f"[{DIM}]{_HELP_FOOTER}[/]"
        )
    else:
        console.print(f"[{DIM}]condition updated (still paused). {_HELP_FOOTER}[/]")
    return True


def _clear(session: Session, console: Console) -> bool:
    if getattr(session, "session_goal", None) is None:
        console.print(f"[{DIM}]no goal to clear.[/]")
        return True
    clear_session_goal(session)
    _persist_goal_state(session)
    console.print(f"[{HIGHLIGHT}]goal cleared.[/]")
    return True


def _cmd_goal(session: Session, console: Console, args: list[str]) -> bool:
    if not args:
        return _show(session, console)
    sub = args[0].lower()
    if sub in {"show", "status"}:
        return _show(session, console)
    if sub == "set":
        return _set(session, console, args[1:])
    if sub == "pause":
        return _pause(session, console)
    if sub == "resume":
        return _resume(session, console)
    if sub == "edit":
        return _edit(session, console, args[1:])
    if sub in {"clear", "unset"}:
        return _clear(session, console)
    # ``/goal <condition>`` is sugar for ``/goal set <condition>``.
    if sub not in _GOAL_SUBCOMMANDS:
        return _set(session, console, args)
    console.print(f"[{ERROR}]usage:[/] {_USAGE}")
    return True
