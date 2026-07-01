"""Shared constants for the Slack send-message tool."""

from __future__ import annotations

from core.domain.types.evidence import EvidenceSource

SOURCE: EvidenceSource = "slack"

# Slack accepts up to 40,000 characters in a message's ``text`` field. Keep a
# small safety margin so a near-limit message plus payload framing never trips
# the API's hard cap.
MAX_MESSAGE_CHARS = 39_000
