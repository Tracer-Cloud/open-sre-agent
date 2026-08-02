"""Stable result shapes for Mattermost message delivery."""

from __future__ import annotations

from typing import Any

from integrations.mattermost.tools.mattermost_send_message_tool.constants import SOURCE
from integrations.mattermost.tools.mattermost_send_message_tool.models import (
    MattermostDeliveryTarget,
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


def sent_result(*, target: MattermostDeliveryTarget, message_length: int) -> dict[str, Any]:
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
