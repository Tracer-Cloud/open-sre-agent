"""Agent-callable Telegram message action."""

from __future__ import annotations

from typing import Any

from core.tool_framework.base import BaseTool
from core.tool_framework.tool_decorator import tool
from integrations.telegram.tools.telegram_send_message_tool.constants import SOURCE
from integrations.telegram.tools.telegram_send_message_tool.delivery import (
    dispatch_message,
    resolve_target,
)
from integrations.telegram.tools.telegram_send_message_tool.results import (
    failed_result,
    sent_result,
)
from integrations.telegram.tools.telegram_send_message_tool.validation import (
    normalize_optional_text,
    validate_message,
)


class TelegramSendMessageTool(BaseTool):
    """Send a plain-text message via the configured Telegram integration."""

    name = "telegram_send_message"
    source = SOURCE
    description = (
        "Send a plain-text message via the configured Telegram integration. "
        "Use this for explicit user-requested Telegram message actions and for "
        "incident notifications. The tool resolves credentials internally and "
        "returns structured delivery status without exposing secrets."
    )
    use_cases = [
        "Sending a user-requested message to the configured Telegram default chat",
        "Posting a concise incident notification to a Telegram chat or channel",
        "Following up after an investigation with a short status update",
    ]
    requires = ["telegram"]
    side_effect_level = "external"
    requires_approval = True
    approval_reason = "Sends a message via Telegram on your behalf."
    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Plain-text message body. Long messages are truncated to Telegram's limit.",
            },
            "chat_id": {
                "type": "string",
                "description": (
                    "Optional Telegram chat or channel id. Defaults to the configured "
                    "default_chat_id when omitted."
                ),
            },
            "reply_to_message_id": {
                "type": "string",
                "description": "Optional Telegram message id to reply to.",
            },
        },
        "required": ["message"],
    }
    outputs = {
        "status": "delivery dispatch status - 'sent' or 'failed'",
        "sent": "boolean delivery result for easy downstream checks",
        "error": "error detail when status is 'failed'",
        "error_type": "stable failure class: validation_error, configuration_error, or delivery_error",
        "chat_id": "Telegram chat id used for delivery",
        "reply_to_message_id": "Telegram message id used for reply threading, when supplied",
        "message_length": "length of the normalized message submitted for delivery",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        telegram = sources.get("telegram") or {}
        return bool(telegram.get("bot_token"))

    # extract_params intentionally stays empty. It is serialized into tool-call
    # traces, so Telegram credentials must be resolved inside run() only.

    def run(
        self,
        message: str,
        chat_id: str = "",
        reply_to_message_id: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        chat_id = normalize_optional_text(chat_id)
        reply_to_message_id = normalize_optional_text(reply_to_message_id)
        valid, normalized_message, validation_error = validate_message(message)
        if not valid:
            return failed_result(
                available=True,
                error=validation_error,
                error_type="validation_error",
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
            )

        target, resolution_error = resolve_target(chat_id, reply_to_message_id)
        if target is None:
            return failed_result(
                available=False,
                error=resolution_error,
                error_type="configuration_error",
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                message_length=len(normalized_message),
            )

        ok, error = dispatch_message(normalized_message, target)
        if not ok:
            return failed_result(
                available=True,
                error=error,
                error_type="delivery_error",
                chat_id=target.chat_id,
                reply_to_message_id=target.reply_to_message_id,
                message_length=len(normalized_message),
            )
        return sent_result(target=target, message_length=len(normalized_message))


telegram_send_message = tool(
    TelegramSendMessageTool(),
    surfaces=("investigation", "action"),
)
