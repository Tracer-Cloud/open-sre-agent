"""Normalize a raw ``buzz feed get`` JSON event into a :class:`BuzzInboundMessage`."""

from __future__ import annotations

from typing import Any

from gateway.transports.buzz.settings import BuzzInboundMessage


def parse_feed_event(raw: dict[str, Any]) -> BuzzInboundMessage | None:
    """Return the normalized event, or ``None`` for a malformed/unusable one."""
    event_id = str(raw.get("id") or "")
    pubkey = str(raw.get("pubkey") or "")
    tags = raw.get("tags")
    if not event_id or not pubkey or not isinstance(tags, list):
        return None

    channel_id = ""
    reply_event_ids: set[str] = set()
    for tag in tags:
        if not isinstance(tag, list) or len(tag) < 2:
            continue
        name, value = tag[0], str(tag[1])
        if name == "h" and not channel_id:
            channel_id = value
        elif name == "e":
            reply_event_ids.add(value)

    if not channel_id:
        return None

    return BuzzInboundMessage(
        event_id=event_id,
        pubkey=pubkey,
        channel_id=channel_id,
        content=str(raw.get("content") or ""),
        created_at=int(raw.get("created_at") or 0),
        reply_event_ids=frozenset(reply_event_ids),
    )


__all__ = ["parse_feed_event"]
