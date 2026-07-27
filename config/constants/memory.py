"""Environment names and static limits for long-term memory."""

from __future__ import annotations

OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV = "OPENSRE_MEMORY_AUTOEXTRACT_DISABLED"
OPENSRE_MEMORY_DIR_ENV = "OPENSRE_MEMORY_DIR"
OPENSRE_MEMORY_DISABLED_ENV = "OPENSRE_MEMORY_DISABLED"
# Opt-in: shared Slack/Telegram gateway hosts keep memory off by default so
# one allowlisted user's facts cannot shape or leak into another user's turns.
OPENSRE_MEMORY_GATEWAY_ENABLED_ENV = "OPENSRE_MEMORY_GATEWAY_ENABLED"

__all__ = [
    "OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV",
    "OPENSRE_MEMORY_DIR_ENV",
    "OPENSRE_MEMORY_DISABLED_ENV",
    "OPENSRE_MEMORY_GATEWAY_ENABLED_ENV",
]
