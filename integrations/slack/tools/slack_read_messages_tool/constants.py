"""Shared constants for the Slack read-messages tool."""

from __future__ import annotations

from core.domain.types.evidence import EvidenceSource

SOURCE: EvidenceSource = "slack"

DEFAULT_MESSAGE_LIMIT = 20
MAX_MESSAGE_LIMIT = 100

# Keep per-message text bounded so a chatty channel cannot flood the context.
MAX_TEXT_CHARS_PER_MESSAGE = 2_000
