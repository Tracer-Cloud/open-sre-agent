"""Registry entrypoint for the Telegram send-message tool."""

from __future__ import annotations

from tools.telegram_send_message_tool.tool import TelegramSendMessageTool, telegram_send_message

TOOL_MODULES = ("tool",)

__all__ = ["TelegramSendMessageTool", "telegram_send_message"]
