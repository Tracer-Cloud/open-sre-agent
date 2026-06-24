"""Base formatting utilities for report generation."""

import html


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


def format_html_link(label: str, url: str | None) -> str:
    """Return a Telegram HTML <a> tag, or escaped plain label without a URL."""
    if not url:
        return html.escape(label)
    safe_label = label.replace("|", "¦").strip() or url
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(safe_label)}</a>'
