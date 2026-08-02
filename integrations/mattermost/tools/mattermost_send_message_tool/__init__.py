"""Registry entrypoint for the Mattermost send-message tool."""

from __future__ import annotations

from integrations.mattermost.tools.mattermost_send_message_tool.tool import (
    MattermostSendMessageTool,
    mattermost_send_message,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "MattermostSendMessageTool", "mattermost_send_message"]
