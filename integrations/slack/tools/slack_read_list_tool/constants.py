"""Constants for the Slack Lists read tool."""

from __future__ import annotations

from core.domain.types.evidence import EvidenceSource

SOURCE: EvidenceSource = "slack"
DEFAULT_ITEM_LIMIT = 50
MAX_ITEM_LIMIT = 100
