"""Base formatting utilities for report generation."""

from __future__ import annotations

import html
import re

from config.constants import SLACK_LINK_RE

__all__ = ["format_html_link", "format_slack_link", "shorten_text", "slack_links_to_plain_text"]


def shorten_text(text: str, max_chars: int = 120, suffix: str = "...") -> str:
    """Shorten text to a maximum length.

    Args:
        text: Text to shorten
        max_chars: Maximum characters in output (including suffix)
        suffix: Suffix to append when truncated

    Returns:
        Shortened text with suffix if truncated
    """
    # Clean up whitespace
    cleaned = " ".join(text.split())

    if len(cleaned) <= max_chars:
        return cleaned

    return cleaned[: max_chars - len(suffix)] + suffix


def format_slack_link(label: str, url: str | None) -> str:
    """Return a Slack-formatted hyperlink, falling back to plain text."""
    if not url:
        return label

    safe_label = label.replace("|", "¦").strip() or url
    return f"<{url}|{safe_label}>"


def slack_links_to_plain_text(text: str) -> str:
    """Convert Slack ``<url|label>`` links to plain ``label (url)``.

    The inverse of :func:`format_slack_link`, for channels that render no markup
    (WhatsApp, SMS, terminal plain-text mode). The URL is kept because
    plain-text clients auto-linkify bare URLs.
    """

    def _repl(match: re.Match[str]) -> str:
        url = str(match.group(1))
        label = match.group(2)
        return f"{label} ({url})" if label else url

    return SLACK_LINK_RE.sub(_repl, text)


def format_html_link(label: str, url: str | None) -> str:
    """Return a Telegram HTML <a> tag, or escaped plain label without a URL."""
    if not url:
        return html.escape(label)
    safe_label = label.replace("|", "¦").strip() or url
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(safe_label)}</a>'
