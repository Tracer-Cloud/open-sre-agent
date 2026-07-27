"""Convert the agent's Markdown output to Slack mrkdwn.

Slack does not render standard Markdown: ``**bold**`` shows literal asterisks,
``#`` headings and ``[text](url)`` links are not recognized. This maps the
common Markdown the LLM emits onto Slack's mrkdwn so replies read cleanly.
"""

from __future__ import annotations

import re

_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+")


def _protect(text: str) -> tuple[str, list[str]]:
    """Replace code spans with placeholders so formatting rules skip them."""
    stash: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        stash.append(match.group(0))
        return f"\x00{len(stash) - 1}\x00"

    text = _CODE_BLOCK_RE.sub(_stash, text)
    text = _INLINE_CODE_RE.sub(_stash, text)
    return text, stash


def _restore(text: str, stash: list[str]) -> str:
    for index, original in enumerate(stash):
        text = text.replace(f"\x00{index}\x00", original)
    return text


def _convert_line(line: str) -> str:
    heading = _HEADING_RE.match(line)
    if heading:
        title = heading.group(1).strip()
        return f"*{title}*" if title else ""
    return _BULLET_RE.sub(lambda m: f"{m.group(1)}• ", line)


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Return ``text`` with common Markdown rewritten as Slack mrkdwn."""
    if not text:
        return text

    protected, stash = _protect(text)

    # Links: [label](url) -> <url|label>
    protected = _LINK_RE.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", protected)
    # Bold: **x** / __x__ -> *x* (Slack bold is a single asterisk)
    protected = _BOLD_RE.sub(lambda m: f"*{m.group(1) or m.group(2)}*", protected)
    # Headings and bullets, line by line.
    protected = "\n".join(_convert_line(line) for line in protected.split("\n"))

    return _restore(protected, stash)
