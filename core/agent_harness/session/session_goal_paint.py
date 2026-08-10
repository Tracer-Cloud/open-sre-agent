"""Progress paint and continuation nudges for outer SessionGoal.

Leaf module: imports :mod:`session_goal` only — do not import this from
``session_goal`` (avoids ``py/cyclic-import``).
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.session_goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    session_goal_elapsed_seconds,
    session_goal_token_delta,
)
from platform.common.evidence_compaction import truncate_message

# Status-line condition shares a Slack/Telegram timeline row with status,
# turn counter, and reason.
_MAX_STATUS_LINE_CONDITION_CHARS = 60


def format_duration_compact(seconds: float) -> str:
    """Human duration for status lines (``45s``, ``1m 23s``, ``1h 02m``)."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_token_count_compact(count: int) -> str:
    """Compact token count (``150``, ``1.2k``, ``3.4M``)."""
    value = max(0, int(count))
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        scaled = value / 1000.0
        text = f"{scaled:.1f}".rstrip("0").rstrip(".")
        return f"{text}k"
    scaled = value / 1_000_000.0
    text = f"{scaled:.1f}".rstrip("0").rstrip(".")
    return f"{text}M"


def derive_session_goal_reason(goal: SessionGoal) -> str:
    """Structured reason for progress paint and continuation nudges.

    No LLM — derived from status + checklist progress so hosts stay honest and
    cheap. Uses :class:`SessionGoalReason` (no tag grammar in the string).
    """
    if goal.status == SessionGoalStatus.ACHIEVED:
        return SessionGoalReason.ACHIEVED_GENERIC
    if goal.status == SessionGoalStatus.BUDGET_EXHAUSTED:
        return SessionGoalReason.budget_exhausted(goal.turns_used, goal.max_outer_turns)
    if goal.status == SessionGoalStatus.CANCELLED:
        return SessionGoalReason.CANCELLED
    if goal.status == SessionGoalStatus.CLEARED:
        return SessionGoalReason.CLEARED
    if goal.checklist:
        done = len(goal.completed & frozenset(range(len(goal.checklist))))
        total = len(goal.checklist)
        nxt = goal.next_checklist_item
        if nxt is None:
            return SessionGoalReason.checklist_progress(done, total)
        _index, item = nxt
        return SessionGoalReason.checklist_progress(done, total, item)
    if goal.host_owned:
        return SessionGoalReason.WAITING_HOST_SIGNAL
    return SessionGoalReason.WAITING_TOOL_EVIDENCE


def refresh_session_goal_reason(goal: SessionGoal) -> SessionGoal:
    """Attach a fresh :func:`derive_session_goal_reason` on ``goal``."""
    return goal.with_reason(derive_session_goal_reason(goal))


def format_session_goal_progress(
    goal: SessionGoal,
    *,
    session: Any | None = None,
    now: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> str:
    """Multi-line progress paint for REPL mid-loop updates and ``/goal show``."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    status = goal.status
    if status == SessionGoalStatus.ACTIVE:
        elapsed = session_goal_elapsed_seconds(goal, now=now)
        duration = format_duration_compact(elapsed) if elapsed is not None else "—"
        tokens = session_goal_token_delta(
            goal,
            session=session,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        token_text = format_token_count_compact(tokens)
        if SessionGoalReason.is_working(reason):
            headline = (
                f"◎ /goal active · working… · {duration} · "
                f"turn {goal.turns_used}/{goal.max_outer_turns} · +{token_text} tokens"
            )
        else:
            headline = (
                f"◎ /goal active · {duration} · turn {goal.turns_used}/{goal.max_outer_turns} "
                f"· +{token_text} tokens"
            )
    else:
        headline = f"Session goal · {status} · turn {goal.turns_used}/{goal.max_outer_turns}"
    lines = [
        headline,
        f"  condition: {goal.condition}",
        f"  reason: {reason}",
    ]
    if not goal.checklist:
        return "\n".join(lines)

    lines.append("  Checklist:")
    next_index = goal.next_checklist_item[0] if goal.next_checklist_item else None
    for index, item in enumerate(goal.checklist):
        done = index in goal.completed
        mark = "[x]" if done else "[ ]"
        prefix = "→ " if (not done and index == next_index) else "  "
        lines.append(f"  {prefix}{mark} {index + 1}. {item}")
    return "\n".join(lines)


def format_session_goal_status_line(
    goal: SessionGoal,
    *,
    session: Any | None = None,
    now: float | None = None,
) -> str:
    """Compact one-line status for gateway sinks (Slack/Telegram timelines)."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    condition = goal.condition.strip()
    condition = truncate_message(condition, _MAX_STATUS_LINE_CONDITION_CHARS)
    if goal.status == SessionGoalStatus.ACTIVE:
        elapsed = session_goal_elapsed_seconds(goal, now=now)
        duration = format_duration_compact(elapsed) if elapsed is not None else "—"
        tokens = format_token_count_compact(session_goal_token_delta(goal, session=session))
        return (
            f"◎ /goal active · {duration} · turn {goal.turns_used}/{goal.max_outer_turns} "
            f"· +{tokens} tok · {condition} · {reason}"
        )
    return (
        f"Goal · {goal.status} · turn {goal.turns_used}/{goal.max_outer_turns} · "
        f"{condition} · {reason}"
    )


def continuation_nudge(goal: SessionGoal) -> str:
    """User-visible follow-up message for the next outer turn."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    reason_block = f"Last progress: {reason}\n\n"
    unfinished = goal.unfinished_items
    if unfinished:
        pending = "\n".join(f"  - [{index}] {item}" for index, item in unfinished)
        return (
            "[session_goal] Continue the active goal without asking whether to "
            f"continue. Goal: {goal.condition}\n\n"
            f"{reason_block}"
            "Unfinished checklist items (0-based indices):\n"
            f"{pending}\n\n"
            "Take the next unfinished item now. When you complete an item, include "
            "`session_goal:done=<index>` (comma-separate multiple). When every "
            "item is done, you may also include `session_goal:achieved`."
        )
    if goal.host_owned:
        return (
            "[session_goal] Continue the active goal without asking whether to "
            f"continue. Goal: {goal.condition}\n\n"
            f"{reason_block}"
            "Answer the condition directly. Do not run `/goal` as a tool. When the "
            "condition is met, include the exact tag `session_goal:achieved` in "
            "your reply (no further tool work required for a host-set goal)."
        )
    return (
        "[session_goal] Continue the active goal without asking whether to "
        f"continue. Goal: {goal.condition}\n\n"
        f"{reason_block}"
        "Take the next unfinished step now. When the goal is met after real tool "
        "work, include the exact tag `session_goal:achieved` in your reply. "
        "Do not emit that tag with no tool evidence — the host will ignore it."
    )


__all__ = [
    "continuation_nudge",
    "derive_session_goal_reason",
    "format_duration_compact",
    "format_session_goal_progress",
    "format_session_goal_status_line",
    "format_token_count_compact",
    "refresh_session_goal_reason",
]
