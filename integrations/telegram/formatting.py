"""Render the agent's Markdown output as Telegram ``parse_mode=HTML``.

Telegram HTML supports a small subset — ``<b> <i> <u> <s> <code> <pre> <a>`` —
and requires ``& < >`` in text to be escaped. This converts the common Markdown
the agent emits (bold, italic, inline code, fenced code, links); anything else is
left as escaped text. Malformed output is the caller's concern: send with a
plain-text fallback so a rejected message is retried unformatted rather than lost.
"""

from __future__ import annotations

import html
import re

_FENCED = re.compile(r"```[^\n]*\n(?P<code>.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])")
_ITALIC_USCORE = re.compile(r"(?<![\w_])_([^_\n]+?)_(?![\w_])")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")


def markdown_to_telegram_html(text: str) -> str:
    """Convert *text* from Markdown to Telegram-safe HTML."""
    stash: list[str] = []

    def _keep(rendered: str) -> str:
        stash.append(rendered)
        return f"\x00{len(stash) - 1}\x00"

    # Pull code out first (placeholders) so its contents are never reformatted.
    text = _FENCED.sub(
        lambda m: _keep(f"<pre>{html.escape(m.group('code'), quote=False)}</pre>"), text
    )
    text = _INLINE.sub(
        lambda m: _keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text
    )

    # Escape the remaining prose, then apply inline formatting.
    text = html.escape(text, quote=False)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC_STAR.sub(r"<i>\1</i>", text)
    text = _ITALIC_USCORE.sub(r"<i>\1</i>", text)
    text = _LINK.sub(r'<a href="\2">\1</a>', text)

    return _PLACEHOLDER.sub(lambda m: stash[int(m.group(1))], text)


__all__ = ["markdown_to_telegram_html"]
