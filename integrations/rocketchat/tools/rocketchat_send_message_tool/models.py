"""Typed models for Rocket.Chat message delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RocketChatDeliveryTarget:
    """Resolved Rocket.Chat delivery destination.

    ``auth_token`` and ``webhook_url`` are deliberately excluded from repr so
    failed assertions, tracebacks, or debug logs do not leak credentials (the
    webhook URL embeds its token).
    """

    mode: Literal["token", "webhook"]
    server_url: str = ""
    auth_token: str = ""
    user_id: str = ""
    channel: str = ""
    webhook_url: str = ""

    @property
    def display_channel(self) -> str:
        """Destination for result payloads — never the webhook URL (it embeds a token)."""
        return self.channel if self.mode == "token" else "<webhook destination>"

    def __repr__(self) -> str:
        return (
            "RocketChatDeliveryTarget("
            f"mode={self.mode!r}, "
            f"server_url={self.server_url!r}, "
            f"user_id={self.user_id!r}, "
            f"channel={self.channel!r}, "
            "auth_token=<redacted>, webhook_url=<redacted>)"
        )
