"""Severity display vocabulary shared across channel renderers.

The SEVERITY_HEADER section carries a raw severity string (e.g. ``"critical"``,
``"warning"``). Each renderer wraps it in its own dialect — Telegram emoji
header, Slack mrkdwn line, Slack Block Kit header + context, Discord embed
title + color. The emoji-to-severity mapping is the same across all three;
consolidating it here prevents drift when, say, a new severity tier gets
added or an emoji gets tweaked.

Channel-specific surface (Discord embed colors, Slack header truncation)
lives in the respective renderer modules.
"""

from __future__ import annotations

# Case-insensitive severity → emoji. Add new aliases here, not in the renderers.
SEVERITY_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "crit": "🔴",
    "high": "🟠",
    "error": "🟠",
    "medium": "🟡",
    "warning": "🟡",
    "warn": "🟡",
    "low": "🟢",
    "info": "🟢",
    "healthy": "🟢",
    "normal": "🟢",
    "none": "⚪",
}

# Fallback for severity strings that don't match a known tier.
DEFAULT_EMOJI = "⚠️"


def severity_emoji(severity: str | None) -> str:
    """Return the emoji for a severity string, case-insensitively.

    Empty/unknown severities fall back to :data:`DEFAULT_EMOJI`.
    """
    if not severity:
        return DEFAULT_EMOJI
    return SEVERITY_EMOJI.get(severity.lower(), DEFAULT_EMOJI)


def severity_display(severity: str | None) -> str:
    """Return a human-display severity string — uppercase, or 'UNKNOWN'."""
    if not severity:
        return "UNKNOWN"
    return severity.upper()
