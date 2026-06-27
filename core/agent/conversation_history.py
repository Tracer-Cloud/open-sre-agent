"""Shared recent-conversation context for interactive-shell prompt builders.

Single source of truth for rendering the recent CLI conversation so the action
planner and the conversational assistant see the same multi-turn history.
"""

from __future__ import annotations

MAX_CONVERSATION_TURNS = 12
MAX_CONVERSATION_MESSAGES = MAX_CONVERSATION_TURNS * 2

NO_HISTORY_PLACEHOLDER = "(no prior messages in this CLI thread)"


def format_recent_conversation(
    messages: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    max_turns: int = MAX_CONVERSATION_TURNS,
) -> str:
    """Render recent CLI-agent turns as ``User:``/``Assistant:`` lines.

    Accepts a list or tuple of ``(role, content)`` pairs (oldest first).
    Returns at most ``max_turns`` turns (oldest first, most recent last).
    Returns :data:`NO_HISTORY_PLACEHOLDER` when empty so prompt builders
    always have a stable, non-empty block. Never raises.
    """
    cap = max(max_turns, 0) * 2
    if not cap:
        return NO_HISTORY_PLACEHOLDER

    lines: list[str] = []
    for entry in messages[-cap:]:
        try:
            role, content = entry
        except (TypeError, ValueError):
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines) if lines else NO_HISTORY_PLACEHOLDER
