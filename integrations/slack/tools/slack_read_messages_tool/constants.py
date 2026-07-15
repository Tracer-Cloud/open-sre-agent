"""Shared constants for the Slack read-messages tool."""

from __future__ import annotations

from core.domain.types.evidence import EvidenceSource

SOURCE: EvidenceSource = "slack"

# Default covers a typical day of channel discussion; the agent can request up
# to MAX for "summarize the whole day" without missing older messages.
DEFAULT_MESSAGE_LIMIT = 50
MAX_MESSAGE_LIMIT = 100

# Keep per-message text bounded so a chatty channel cannot flood the context.
MAX_TEXT_CHARS_PER_MESSAGE = 2_000
