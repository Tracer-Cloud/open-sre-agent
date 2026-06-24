"""Prompt composition and sanitization for planner LLM calls."""

from __future__ import annotations

import re
from typing import Any

from app.cli.interactive_shell.prompting.conversation_history import format_recent_conversation

from .constants import _MAX_TEXT_LEN
from .system_prompt import _SYSTEM_PROMPT_BASE


def _system_prompt() -> str:
    return _SYSTEM_PROMPT_BASE


def _connected_integrations_block(session: Any | None) -> str:
    """Render which integrations are connected for THIS turn.

    The planner gates *implicit* diagnostic questions (no explicit "investigate"
    verb) on this line: it may dispatch an investigation only when at least one
    integration is connected; with "none" or "unknown" it hands off instead.
    Explicit investigate instructions are NOT gated and dispatch regardless.
    """
    known = bool(getattr(session, "configured_integrations_known", False))
    configured = tuple(getattr(session, "configured_integrations", ()) or ())
    if known and configured:
        listing = ", ".join(sorted(str(name) for name in configured))
    elif known:
        listing = "none"
    else:
        listing = "unknown"
    return f"CONNECTED INTEGRATIONS (this install, right now): {listing}\n\n"


def _recent_conversation_block(session: Any | None) -> str:
    """Render the shared recent-conversation context for the planner prompt.

    Uses the same source of truth as the conversational assistant so the planner
    can resolve follow-up references (e.g. "do both") against the assistant's
    previous reply. The final USER MESSAGE — not this block — is what to act on.
    """
    history = format_recent_conversation(session)
    return (
        "RECENT CONVERSATION (context only, oldest first; use it ONLY to resolve "
        "follow-up references in the USER MESSAGE below — do NOT re-run turns that "
        f"already completed):\n{history}\n\n"
    )


def _sanitise_text(text: str) -> str:
    sanitised = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    sanitised = re.sub(r"<{3,}|>{3,}", " ", sanitised)
    return sanitised[:_MAX_TEXT_LEN]
