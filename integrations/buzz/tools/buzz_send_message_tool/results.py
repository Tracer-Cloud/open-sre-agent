"""Stable result shapes for Buzz message delivery."""

from __future__ import annotations

from typing import Any

from integrations.buzz.tools.buzz_send_message_tool.constants import SOURCE
from integrations.buzz.tools.buzz_send_message_tool.models import BuzzDeliveryTarget


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


def sent_result(*, target: BuzzDeliveryTarget, message_length: int) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "available": True,
        "status": "sent",
        "sent": True,
        "error": "",
        "error_type": "",
        "channel": target.channel,
        "message_length": message_length,
    }
