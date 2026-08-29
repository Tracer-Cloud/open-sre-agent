"""Progress / status-line formatting for SessionGoal.

Leaf module: presentation only. Domain reason derive lives in
:mod:`core.agent_harness.session_goal.goal`; continuation prompts in
:mod:`core.agent_harness.session_goal.continuation`. Do not import this from
``goal`` (avoids ``py/cyclic-import``).
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    derive_session_goal_reason,
    session_goal_elapsed_seconds,
    session_goal_token_delta,
)
from infrastructure.evidence.evidence_compaction import truncate_message

# Leading mark for user-visible ``/goal`` progress lines (REPL + gateway).
SESSION_GOAL_PROGRESS_MARK = "◎"
# User-facing slash name — progress text never says ``SessionGoal``.
SESSION_GOAL_USER_WORD = "/goal"

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
    # Branch on the rounded-to-one-decimal magnitude, not the raw value: a
    # count like 999_950 rounds to "1000.0" at .1f precision, and comparing
    # the raw value against 1_000_000 let that render as the broken "1000k"
    # instead of rolling into the M branch below.
    if round(value / 1000.0, 1) < 1000:
        scaled = value / 1000.0
        text = f"{scaled:.1f}".rstrip("0").rstrip(".")
        return f"{text}k"
    scaled = value / 1_000_000.0
    text = f"{scaled:.1f}".rstrip("0").rstrip(".")
    return f"{text}M"


def _progress_headline_active_or_paused(
    goal: SessionGoal,
    *,
    label: str,
    session: Any | None,
    now: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    reason: str,
) -> str:
    elapsed = session_goal_elapsed_seconds(goal, now=now)
    duration = format_duration_compact(elapsed) if elapsed is not None else "—"
    tokens = session_goal_token_delta(
        goal,
        session=session,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    token_text = format_token_count_compact(tokens)
    mark = SESSION_GOAL_PROGRESS_MARK
    word = SESSION_GOAL_USER_WORD
    if label == "active" and SessionGoalReason.is_working(reason):
        return (
            f"{mark} {word} active · working… · {duration} · "
            f"turn {goal.turns_used}/{goal.max_outer_turns} · +{token_text} tokens"
        )
    return (
        f"{mark} {word} {label} · {duration} · turn {goal.turns_used}/{goal.max_outer_turns} "
        f"· +{token_text} tokens"
    )


def format_session_goal_progress(
    goal: SessionGoal,
    *,
    session: Any | None = None,
    now: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> str:
    """Multi-line progress text for REPL mid-loop updates and ``/goal show``."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    status = goal.status
    if status == SessionGoalStatus.ACTIVE:
        headline = _progress_headline_active_or_paused(
            goal,
            label="active",
            session=session,
            now=now,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reason=reason,
        )
    elif status == SessionGoalStatus.PAUSED:
        headline = _progress_headline_active_or_paused(
            goal,
            label="paused",
            session=session,
            now=now,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reason=reason,
        )
    else:
        headline = (
            f"{SESSION_GOAL_PROGRESS_MARK} {SESSION_GOAL_USER_WORD} {status} · "
            f"turn {goal.turns_used}/{goal.max_outer_turns}"
        )
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
    mark = SESSION_GOAL_PROGRESS_MARK
    word = SESSION_GOAL_USER_WORD
    if goal.status == SessionGoalStatus.ACTIVE:
        elapsed = session_goal_elapsed_seconds(goal, now=now)
        duration = format_duration_compact(elapsed) if elapsed is not None else "—"
        tokens = format_token_count_compact(session_goal_token_delta(goal, session=session))
        return (
            f"{mark} {word} active · {duration} · turn {goal.turns_used}/{goal.max_outer_turns} "
            f"· +{tokens} tok · {condition} · {reason}"
        )
    if goal.status == SessionGoalStatus.PAUSED:
        elapsed = session_goal_elapsed_seconds(goal, now=now)
        duration = format_duration_compact(elapsed) if elapsed is not None else "—"
        tokens = format_token_count_compact(session_goal_token_delta(goal, session=session))
        return (
            f"{mark} {word} paused · {duration} · turn {goal.turns_used}/{goal.max_outer_turns} "
            f"· +{tokens} tok · {condition} · {reason}"
        )
    return (
        f"{mark} {word} {goal.status} · turn {goal.turns_used}/{goal.max_outer_turns} · "
        f"{condition} · {reason}"
    )


def is_session_goal_progress_text(text: str) -> bool:
    """True when ``text`` is ``/goal`` status chrome, not an assistant answer.

    The ``/goal set`` attach turn may run ``slash_invoke`` (counts as tool
    evidence) and capture the progress status block as the turn "reply". Host
    evaluate must not treat that as a completed answer.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if SessionGoalReason.WAITING_HOST_SIGNAL in stripped:
        return True
    progress_lead = f"{SESSION_GOAL_PROGRESS_MARK} {SESSION_GOAL_USER_WORD}"
    return progress_lead in stripped


__all__ = [
    "SESSION_GOAL_PROGRESS_MARK",
    "SESSION_GOAL_USER_WORD",
    "format_duration_compact",
    "format_session_goal_progress",
    "format_session_goal_status_line",
    "format_token_count_compact",
    "is_session_goal_progress_text",
]
