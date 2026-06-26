"""Shared recent-conversation context for interactive-shell prompt builders.

Single source of truth for rendering the recent CLI conversation so the action
planner and the conversational assistant see the *same* multi-turn history. That
shared context is what lets the planner resolve anaphoric follow-ups like
"do both", "do that", or "yes" against the assistant's previous reply instead of
guessing an unrelated action.

The backing store is ``session.cli_agent_messages`` — a list of ``(role, content)``
pairs holding two entries (user + assistant) per turn.
"""

from __future__ import annotations

# Cap on retained conversation turns. The backing list holds two entries
# (user + assistant) per turn, so the message cap is twice this.
MAX_CONVERSATION_TURNS = 12
MAX_CONVERSATION_MESSAGES = MAX_CONVERSATION_TURNS * 2

NO_HISTORY_PLACEHOLDER = "(no prior messages in this CLI thread)"


def format_recent_conversation(
    session: object | None,
    *,
    max_turns: int = MAX_CONVERSATION_TURNS,
) -> str:
    """Render the most recent CLI-agent turns as ``User:``/``Assistant:`` lines.

    Reads ``session.cli_agent_messages`` and returns at most ``max_turns`` turns
    (oldest first, most recent last). Returns :data:`NO_HISTORY_PLACEHOLDER` when
    there is no prior turn so prompt builders always have a stable, non-empty
    block. Never raises: a missing or malformed history yields the placeholder.
    """
    messages = getattr(session, "cli_agent_messages", None) or []
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
