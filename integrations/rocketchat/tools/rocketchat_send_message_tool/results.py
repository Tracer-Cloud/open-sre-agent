"""Stable result shapes for Rocket.Chat message delivery."""

from __future__ import annotations

from typing import Any

from integrations.rocketchat.tools.rocketchat_send_message_tool.constants import SOURCE
from integrations.rocketchat.tools.rocketchat_send_message_tool.models import (
    RocketChatDeliveryTarget,
)


def failed_result(
    *,
    available: bool,
    error: str,
    error_type: str,
    channel: str = "",
    message_length: int = 0,
) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "available": available,
        "status": "failed",
        "sent": False,
        "error": error,
        "error_type": error_type,
        "channel": channel,
        "message_length": message_length,
    }


def sent_result(*, target: RocketChatDeliveryTarget, message_length: int) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "available": True,
        "status": "sent",
        "sent": True,
        "error": "",
        "error_type": "",
        "channel": target.display_channel,
        "message_length": message_length,
    }
