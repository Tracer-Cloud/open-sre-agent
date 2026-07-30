"""Seed gateway sessions from Discord thread history when local state is empty."""

from __future__ import annotations

import logging

from core.agent_harness.session import SessionCore

logger = logging.getLogger(__name__)


def session_needs_thread_seed(text: str, *, is_reply: bool) -> bool:
    """Whether to pull Discord thread history into the session before the turn."""
    _ = text
    return is_reply


def seed_session_from_discord_thread(
    session: SessionCore,
    *,
    history: list[tuple[str, str]],
) -> int:
    """Append prior thread messages as user/assistant pairs. Returns count added."""
    if not history:
        return 0
    messages = list(getattr(session, "cli_agent_messages", []) or [])
    if messages:
        return 0
    for role, content in history:
        if not content.strip():
            continue
        messages.append({"role": role, "content": content})
    session.cli_agent_messages = messages
    return len(history)
