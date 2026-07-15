"""Parse Slack Events API payloads into normalized inbound messages."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_LEADING_MENTION = re.compile(r"^\s*<@[^>]+>\s*")

_DM_CHANNEL_TYPE = "im"

# Message subtypes that still carry a real user mention/message to answer.
# Everything else with a subtype (edits, joins, channel bookkeeping) is ignored.
_HANDLED_MESSAGE_SUBTYPES = frozenset({"file_share", "thread_broadcast"})


@dataclass(frozen=True)
class SlackInboundMessage:
    """Normalized inbound Slack mention or DM text."""

    team_id: str
    user_id: str
    channel_id: str
    ts: str
    thread_ts: str
    text: str

    @property
    def conversation_key(self) -> str:
        """Session binding key: one conversation per Slack thread."""
        return f"{self.team_id}:{self.channel_id}:{self.thread_ts}"


def parse_events_api_payload(payload: Mapping[str, Any]) -> SlackInboundMessage | None:
    """Return the inbound message for an ``events_api`` envelope payload.

    Accepts ``app_mention`` events (channels) and plain ``message`` events in
    DMs, including ``file_share`` / ``thread_broadcast`` subtypes that still
    carry a real mention. Returns ``None`` for anything else — bot echoes,
    bookkeeping subtypes (edits, joins), and events missing required fields.
    """
    event = payload.get("event")
    if not isinstance(event, Mapping):
        return None
    subtype = event.get("subtype")
    if event.get("bot_id") or (subtype and subtype not in _HANDLED_MESSAGE_SUBTYPES):
        return None

    event_type = event.get("type")
    is_mention = event_type == "app_mention"
    is_dm = event_type == "message" and event.get("channel_type") == _DM_CHANNEL_TYPE
    if not (is_mention or is_dm):
        return None

    team_id = str(payload.get("team_id") or event.get("team") or "")
    user_id = str(event.get("user") or "")
    channel_id = str(event.get("channel") or "")
    ts = str(event.get("ts") or "")
    text = _LEADING_MENTION.sub("", str(event.get("text") or "")).strip()
    if not (team_id and user_id and channel_id and ts and text):
        return None

    thread_ts = str(event.get("thread_ts") or ts)
    return SlackInboundMessage(
        team_id=team_id,
        user_id=user_id,
        channel_id=channel_id,
        ts=ts,
        thread_ts=thread_ts,
        text=text,
    )
