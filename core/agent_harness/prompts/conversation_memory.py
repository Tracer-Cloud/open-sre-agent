"""Shared recent-conversation context for interactive-shell prompt builders.

Single source of truth for rendering the recent CLI conversation so the action
planner and the conversational assistant see the same multi-turn history.
"""

from __future__ import annotations

import re

from core.state import MAX_CONVERSATION_TURNS
from core.state.transcript_window import SESSION_SUMMARY_PREFIX
from platform.harness_ports import strip_message_context_prefix

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
_AFFIRMATIVE_RE = re.compile(
    r"(?is)^\s*(?:yes|y|yeah|yep|yup|sure|ok|okay|please|go ahead|do it|do that)"
    r"(?:\s*please)?\s*[.!?]?\s*$"
)
# Slack users often restate the offer instead of a bare yes.
_AFFIRMATIVE_RESTATED_RE = re.compile(
    r"(?is)(?:want\s+me\s+to\s*:.*\byes\b|\bi replied yes\b|"
    r"you asked .*\byes\b|\bas a yes\b)"
)
_WANT_ME_TO_RE = re.compile(
    r"(?is)\*{0,2}Want me to:\*{0,2}\s*(.+?)(?:\n\s*\n|\Z)",
)
_OR_SPLIT_RE = re.compile(r"(?i),\s*or\s+|\s+or\s+")


def expand_affirmative_follow_up(
    text: str,
    messages: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None,
) -> str:
    """Rewrite bare affirmatives into the prior ``Want me to:`` offer.

    Gateway/Slack turns often arrive as ``yes`` / ``sure`` after the assistant
    offered a next step. Without expansion, the action agent treats that as a
    new vague request and hands off to the investigate-onboarding assistant.
    Preserves any leading ``[Slack …]`` context line for channel targeting.
    """
    raw = text if isinstance(text, str) else ""
    if not raw.strip() or not messages:
        return raw

    prefix, remainder = strip_message_context_prefix(raw)
    if not (_AFFIRMATIVE_RE.match(remainder) or _AFFIRMATIVE_RESTATED_RE.search(remainder)):
        return raw

    offer = _latest_want_me_to_offer(messages)
    if not offer:
        return raw
    return f"{prefix}Yes — please {_normalize_offer(offer)}."


def _normalize_offer(offer: str) -> str:
    """Collapse dual ``A, or B`` Want-me-to offers into an actionable request."""
    parts = [p.strip(" .?") for p in _OR_SPLIT_RE.split(offer, maxsplit=1) if p.strip()]
    if len(parts) == 2:
        return f"do both — {parts[0]}; and {parts[1]}"
    return offer


def _latest_want_me_to_offer(
    messages: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> str | None:
    for entry in reversed(messages):
        try:
            role, content = entry
        except (TypeError, ValueError):
            continue
        if role != "assistant" or not isinstance(content, str):
            continue
        # A compacted session summary can quote an old offer; only live
        # assistant messages carry an actionable "Want me to:".
        if content.startswith(SESSION_SUMMARY_PREFIX):
            continue
        match = _WANT_ME_TO_RE.search(content)
        if not match:
            continue
        offer = match.group(1).strip().rstrip("?").strip()
        if offer:
            return offer
    return None


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
    limit = max_entries if max_entries > 0 else None
    # Walk newest-first so we can stop as soon as `limit` facts are found,
    # instead of scanning the full history and trimming afterward.
    for entry in reversed(messages):
        try:
            role, content = entry
        except (TypeError, ValueError):
            continue
        if role != "assistant" or not isinstance(content, str):
            continue
        # A compacted session summary quotes old tool output wholesale; letting
        # it in as one giant "fact" can spend the whole budget and starve
        # newer live results. The summary reaches the model via the history
        # block instead.
        if content.startswith(SESSION_SUMMARY_PREFIX):
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
        if limit is not None and len(facts) >= limit:
            break

    if not facts:
        return ""

    rendered: list[str] = []
    remaining = max(max_chars, 0)
    # `facts` was collected newest-first; reverse back to maintain chronological order
    for idx, fact in enumerate(reversed(facts), start=1):
        if remaining <= 0:
            break
        chunk = f"- Prior assistant/tool output {idx}:\n{fact.strip()}"
        if len(chunk) > remaining:
            chunk = chunk[:remaining].rstrip() + "\n...[truncated]"
        rendered.append(chunk)
        remaining -= len(chunk) + 2
    return "\n\n".join(rendered)
