"""Agent-callable Slack message action."""

from __future__ import annotations

import os
from typing import Any

from core.tool_framework.base import BaseTool
from core.tool_framework.tool_decorator import tool
from integrations.slack.tools.slack_send_message_tool.constants import SOURCE
from integrations.slack.tools.slack_send_message_tool.delivery import (
    dispatch_message,
    resolve_webhook_url,
)
from integrations.slack.tools.slack_send_message_tool.results import failed_result, sent_result
from integrations.slack.tools.slack_send_message_tool.validation import validate_message


class SlackSendMessageTool(BaseTool):
    """Send a plain-text message via the configured Slack incoming webhook."""

    name = "slack_send_message"
    source = SOURCE
    description = (
        "Send a plain-text message to Slack via the configured incoming webhook. "
        "Use this for explicit user-requested Slack notifications, status updates, "
        "or on-demand alerts. The tool resolves the webhook URL internally and "
        "returns structured delivery status without exposing secrets."
    )
    use_cases = [
        "Sending a user-requested notification to the configured Slack channel",
        "Posting a concise status update after an investigation or action",
        "Alerting a team channel with a short on-demand message",
    ]
    anti_examples = [
        "Publishing a full RCA report (use the investigation publish flow instead)",
        "Replying in an existing Slack thread without thread context",
    ]
    requires = ["slack"]
    side_effect_level = "external"
    requires_approval = True
    approval_reason = "Sends a message to Slack on your behalf."
    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": (
                    "Plain-text message body. Long messages are truncated to Slack's limit."
                ),
            },
            "webhook_url": {
                "type": "string",
                "description": (
                    "Optional Slack incoming webhook URL. Defaults to SLACK_WEBHOOK_URL or "
                    "the configured Slack integration when omitted."
                ),
            },
        },
        "required": ["message"],
        "additionalProperties": False,
    }
    outputs = {
        "status": "delivery dispatch status - 'sent' or 'failed'",
        "sent": "boolean delivery result for easy downstream checks",
        "error": "error detail when status is 'failed'",
        "error_type": "stable failure class: validation_error, configuration_error, or delivery_error",
        "message_length": "length of the normalized message submitted for delivery",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        slack = sources.get("slack") or {}
        configured_webhook = str(slack.get("webhook_url") or "").strip()
        if not configured_webhook and isinstance(slack.get("config"), dict):
            configured_webhook = str(slack["config"].get("webhook_url") or "").strip()
        env_webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        return bool(configured_webhook or env_webhook)

    # extract_params intentionally stays empty. It is serialized into tool-call
    # traces, so Slack webhook URLs must be resolved inside run() only.

    def run(
        self,
        message: str,
        webhook_url: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        valid, normalized_message, validation_error = validate_message(message)
        if not valid:
            return failed_result(
                available=True,
                error=validation_error,
                error_type="validation_error",
            )

        target, resolution_error = resolve_webhook_url(str(webhook_url or "").strip())
        if target is None:
            return failed_result(
                available=False,
                error=resolution_error,
                error_type="configuration_error",
                message_length=len(normalized_message),
            )

        ok, error = dispatch_message(normalized_message, target)
        if not ok:
            return failed_result(
                available=True,
                error=error,
                error_type="delivery_error",
                message_length=len(normalized_message),
            )
        return sent_result(message_length=len(normalized_message))


slack_send_message = tool(
    SlackSendMessageTool(),
    surfaces=("investigation", "action"),
)
