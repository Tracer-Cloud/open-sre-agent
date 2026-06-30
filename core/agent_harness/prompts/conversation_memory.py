"""Shared recent-conversation context for interactive-shell prompt builders.

Single source of truth for rendering the recent CLI conversation so the action
planner and the conversational assistant see the same multi-turn history.
"""

from __future__ import annotations

import re

MAX_CONVERSATION_TURNS = 12
MAX_CONVERSATION_MESSAGES = MAX_CONVERSATION_TURNS * 2

NO_HISTORY_PLACEHOLDER = "(no prior messages in this CLI thread)"
_ACTION_FACT_MARKERS = (
    " input:",
    " result:",
    "tool:",
    "arguments:",
    "stdout",
    "response_text",
)
_VALUE_LINE_RE = re.compile(
    r"(?im)^[A-Z][A-Za-z0-9 ._/-]{1,64}:\s+.*(?:[-+]?\d+(?:\.\d+)?\s*°?\s*[CF]|sent|true|false|\{|\[)"
)


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


def format_prior_action_facts(
    messages: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    max_entries: int = 6,
    max_chars: int = 4_000,
) -> str:
    """Render a compact fact block from earlier assistant/tool outputs.

    The persisted conversation is the source of truth. This view only makes the
    actionable parts easier for the next prompt to use: tool inputs/results,
    command stdout, and value-shaped lines such as weather readings.
    """
    facts: list[str] = []
    for entry in messages:
        try:
            role, content = entry
        except (TypeError, ValueError):
            continue
        if role != "assistant" or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        lower = text.lower()
        if not any(
            marker in lower for marker in _ACTION_FACT_MARKERS
        ) and not _VALUE_LINE_RE.search(text):
            continue
        facts.append(text)

    if not facts:
        return ""

    rendered: list[str] = []
    remaining = max(max_chars, 0)
    for idx, fact in enumerate(facts[-max_entries:], start=1):
        if remaining <= 0:
            break
        chunk = f"- Prior assistant/tool output {idx}:\n{fact.strip()}"
        if len(chunk) > remaining:
            chunk = chunk[:remaining].rstrip() + "\n...[truncated]"
        rendered.append(chunk)
        remaining -= len(chunk) + 2
    return "\n\n".join(rendered)
