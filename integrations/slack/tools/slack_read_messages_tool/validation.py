"""Input normalization and validation for Slack channel refs."""

from __future__ import annotations

from integrations.slack.tools.slack_read_messages_tool.constants import (
    DEFAULT_MESSAGE_LIMIT,
    MAX_MESSAGE_LIMIT,
)
from integrations.slack.web_client import normalize_channel_ref


def validate_channel_id(channel_id: str) -> tuple[bool, str, str]:
    """Return ``(is_valid, normalized_ref, error)``.

    Accepts Slack IDs (``C…`` / ``D…`` / ``G…``) or ``#channel-name``.
    """
    return normalize_channel_ref(channel_id)


def clamp_limit(limit: object) -> int:
    try:
        value = int(str(limit))
    except (TypeError, ValueError):
        return DEFAULT_MESSAGE_LIMIT
    return max(1, min(value, MAX_MESSAGE_LIMIT))
