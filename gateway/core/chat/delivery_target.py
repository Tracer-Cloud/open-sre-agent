"""Chat delivery target for addressing messages after investigation completion."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatDeliveryTarget:
    """Address information for delivering investigation results to a chat thread.

    This captures enough information to post back to the originating conversation
    after the investigation completes on a background worker, when the original
    turn is long gone.
    """

    platform: str  # "slack", "discord", "telegram"
    channel_id: str  # Channel, DM, or group ID
    thread_ts: str | None = None  # Thread timestamp for threaded replies (Slack)
    user_id: str | None = None  # Target user for DMs or mentions
    origin_message_id: str | None = None  # Message that triggered the investigation
