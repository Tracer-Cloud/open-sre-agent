"""Registry entrypoint for the Buzz send-message tool."""

from __future__ import annotations

from integrations.buzz.tools.buzz_send_message_tool.tool import (
    BuzzSendMessageTool,
    buzz_send_message,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "BuzzSendMessageTool", "buzz_send_message"]
