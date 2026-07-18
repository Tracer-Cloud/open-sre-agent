"""Registry entrypoint for the Rocket.Chat send-message tool."""

from __future__ import annotations

from integrations.rocketchat.tools.rocketchat_send_message_tool.tool import (
    RocketChatSendMessageTool,
    rocketchat_send_message,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "RocketChatSendMessageTool", "rocketchat_send_message"]
