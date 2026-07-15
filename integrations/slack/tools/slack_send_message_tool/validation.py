"""Input normalization and validation for Slack message actions."""

from __future__ import annotations

from integrations.slack.tools.slack_send_message_tool.constants import MAX_MESSAGE_CHARS


def validate_message(message: str) -> tuple[bool, str, str]:
    """Normalize and validate a Slack message body.

    Returns ``(is_valid, normalized_message, error)``. Over-long messages are
    truncated to Slack's limit rather than rejected.
    """
    normalized = str(message or "").strip()
    if not normalized:
        return False, "", "Message cannot be empty."
    if len(normalized) > MAX_MESSAGE_CHARS:
        normalized = normalized[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return True, normalized, ""
