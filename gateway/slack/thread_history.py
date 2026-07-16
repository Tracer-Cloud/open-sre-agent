"""Seed gateway session history from the live Slack thread when needed.

Session JSONL can be empty across redeploys / ephemeral disks / new bindings
while the Slack thread still holds the prior assistant ``Want me to:`` offer.
Seeding from ``conversations.replies`` makes follow-ups like ``yes`` resolve.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from integrations.slack.bot_api import (
    fetch_channel_messages,
    resolve_bot_token,
)

logger = logging.getLogger(__name__)

_ASSISTANT_SHAPE_RE = re.compile(
    r"(?i)(?:\*\*)?I found:(?:\*\*)?|(?:\*\*)?Want me to:(?:\*\*)?|"
    r"(?:\*\*)?Here's what that looks like:(?:\*\*)?"
)
_THREAD_SEED_LIMIT = 40


def session_needs_thread_seed(user_text: str, *, is_reply: bool = False) -> bool:
    """True when follow-up resolution needs Slack thread history.

    The Slack thread is the source of truth for a conversation (gateway session
    files are ephemeral across redeploys). So ANY threaded reply re-seeds from
    the live thread — this covers every follow-up shape ("yes", "do that",
    "the first one", "group by role") without maintaining a phrase list, and a
    repeated affirmative resolves against the LATEST assistant offer. A brand-new
    top-level mention (not a reply) starts fresh and needs no seed.
    """
    if is_reply:
        return True
    bare = str(user_text or "").strip()
    if not bare:
        return False
    lower = bare.lower()
    if lower in {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "please"}:
        return True
    return "want me to" in lower and re.search(r"\byes\b", lower) is not None


def messages_from_slack_thread(
    *,
    channel_id: str,
    thread_ts: str,
    exclude_ts: str = "",
    bot_user_id: str = "",
) -> list[tuple[str, str]]:
    """Fetch thread replies and map them to ``(role, content)`` pairs."""
    target, err = resolve_bot_token()
    if target is None:
        logger.debug("slack thread seed skipped: %s", err)
        return []
    raw, fetch_err = fetch_channel_messages(
        target,
        channel_id=channel_id,
        limit=_THREAD_SEED_LIMIT,
        thread_ts=thread_ts,
    )
    if raw is None:
        logger.debug("slack thread seed fetch failed: %s", fetch_err)
        return []

    skip = str(exclude_ts or "").strip()
    bot = str(bot_user_id or "").strip()
    out: list[tuple[str, str]] = []
    for item in raw:
        ts = str(item.get("ts") or "")
        if skip and ts == skip:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        user = str(item.get("user") or "")
        role = "assistant" if _is_assistant_message(text, user=user, bot_user_id=bot) else "user"
        if role == "user" and user:
            # Attribute the speaker: multi-user threads collapse into one
            # anonymous "user" otherwise, so the agent cannot tell who said
            # what ("call me X" then someone else asks "what is my name?").
            text = f"<@{user}>: {text}"
        out.append((role, text))
    return out


def seed_session_from_slack_thread(
    session: Any,
    *,
    channel_id: str,
    thread_ts: str,
    exclude_ts: str = "",
    bot_user_id: str = "",
) -> int:
    """Replace empty/incomplete session transcript with Slack thread turns.

    Returns the number of messages seeded.
    """
    seeded = messages_from_slack_thread(
        channel_id=channel_id,
        thread_ts=thread_ts,
        exclude_ts=exclude_ts,
        bot_user_id=bot_user_id,
    )
    if not seeded:
        return 0
    session.cli_agent_messages = seeded
    return len(seeded)


def _is_assistant_message(text: str, *, user: str, bot_user_id: str) -> bool:
    if bot_user_id and user == bot_user_id:
        return True
    return bool(_ASSISTANT_SHAPE_RE.search(text))
