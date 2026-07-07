"""Public Telegram formatting entrypoints."""

from __future__ import annotations

from integrations.telegram.markdown import (
    looks_like_telegram_html,
    render_markdown_as_telegram_html,
)


def markdown_to_telegram_html(text: str) -> str:
    """Convert *text* from Markdown / Slack mrkdwn to Telegram-safe HTML."""
    if looks_like_telegram_html(text):
        return text
    return render_markdown_as_telegram_html(text)


__all__ = ["looks_like_telegram_html", "markdown_to_telegram_html"]
