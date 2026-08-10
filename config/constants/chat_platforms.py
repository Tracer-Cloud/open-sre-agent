"""Shared constants for chat platform names.

These constants ensure consistent platform keys between registration
and delivery target resolution, preventing silent lookup misses.
"""

from __future__ import annotations

# Platform keys for chat notifier registry and delivery targets
PLATFORM_SLACK = "slack"
PLATFORM_DISCORD = "discord"
PLATFORM_TELEGRAM = "telegram"
