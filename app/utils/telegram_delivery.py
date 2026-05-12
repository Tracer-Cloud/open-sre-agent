"""Telegram delivery helper - posts investigation findings to Telegram Bot API."""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

from app.utils.delivery_transport import post_json
from app.utils.truncation import truncate

logger = logging.getLogger(__name__)

_MESSAGE_LIMIT = 4096
_BOT_TOKEN_RE = re.compile(r"(bot)[^/]+(/)")
_SIMPLE_TAG_NAMES = frozenset({"b", "i", "u", "code"})


def _strip_trailing_incomplete_tag(chunk: str) -> str:
    """Drop a trailing ``<``… fragment with no closing ``>``."""
    while True:
        last_lt = chunk.rfind("<")
        if last_lt == -1:
            return chunk
        if ">" in chunk[last_lt:]:
            return chunk
        chunk = chunk[:last_lt].rstrip()


def _strip_trailing_partial_entity(chunk: str) -> str:
    """Remove a trailing ``&``… suffix that is not a complete character reference."""
    amp = chunk.rfind("&")
    if amp == -1:
        return chunk
    tail = chunk[amp:]
    if ";" in tail:
        return chunk
    return chunk[:amp].rstrip()


def _balance_telegram_markup_tags(fragment: str) -> str:
    """Append closing tags for open ``b``/``i``/``u``/``code``/``a`` elements at end of *fragment*."""
    stack: list[str] = []
    pos = 0
    while pos < len(fragment):
        if fragment[pos] != "<":
            pos += 1
            continue
        end = fragment.find(">", pos)
        if end == -1:
            break
        raw = fragment[pos : end + 1]
        low = raw.lower()
        if low.startswith("<a ") and not low.startswith("</"):
            stack.append("a")
        elif low == "</a>":
            if stack and stack[-1] == "a":
                stack.pop()
        elif low.startswith("</"):
            name = low[2:-1].strip().lower()
            if name in _SIMPLE_TAG_NAMES and stack and stack[-1] == name:
                stack.pop()
        elif not low.startswith("<a ") and low[1] != "/":
            name = low[1:-1].strip().lower().split()[0]
            if name in _SIMPLE_TAG_NAMES:
                stack.append(name)
        pos = end + 1

    return fragment + "".join(f"</{t}>" for t in reversed(stack))


def truncate_for_telegram_html(text: str, max_len: int, suffix: str = "…") -> str:
    """Truncate *text* for Telegram ``parse_mode=HTML`` without breaking markup at the cut."""
    if len(text) <= max_len:
        return text
    if max_len <= len(suffix):
        return suffix[:max_len]
    room = max_len - len(suffix)
    while room > 0:
        chunk = text[:room]
        chunk = _strip_trailing_incomplete_tag(chunk)
        chunk = _strip_trailing_partial_entity(chunk)
        chunk = _balance_telegram_markup_tags(chunk)
        candidate = chunk + suffix
        if len(candidate) <= max_len:
            return candidate
        room -= 1
    return suffix[:max_len]


def _redact_arg(a: object) -> object:
    """Redact bot token from a log arg, preserving the original type if no match."""
    s = str(a)
    redacted = _BOT_TOKEN_RE.sub(r"\1<redacted>\2", s)
    return redacted if redacted != s else a


class _TelegramTokenFilter(logging.Filter):
    """Scrub Telegram bot tokens from httpx/httpcore log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _BOT_TOKEN_RE.sub(r"\1<redacted>\2", str(record.msg))
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        return True


def _install_httpx_token_filter() -> None:
    _filter = _TelegramTokenFilter()
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).addFilter(_filter)


_install_httpx_token_filter()


def _redact_token(text: str, bot_token: str) -> str:
    """Replace bot token with <redacted> to prevent accidental log/error leakage."""
    if bot_token and bot_token in text:
        return text.replace(bot_token, "<redacted>")
    return text


def post_telegram_message(
    chat_id: str,
    text: str,
    bot_token: str,
    parse_mode: str = "",
    reply_to_message_id: str = "",
    reply_markup: dict[str, Any] | None = None,
) -> tuple[bool, str, str]:
    """Call Telegram Bot API sendMessage endpoint.

    Returns (success, error, message_id).
    """
    logger.debug("[telegram] post message params chat_id: %s", chat_id)
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id and reply_to_message_id != "0":
        with contextlib.suppress(ValueError, TypeError):
            payload["reply_to_message_id"] = int(reply_to_message_id)
    response = post_json(
        url=f"https://api.telegram.org/bot{bot_token}/sendMessage",
        payload=payload,
    )
    if not response.ok:
        error = _redact_token(response.error, bot_token)
        logger.warning("[telegram] post message exception: %s", error)
        return False, error, ""
    if response.status_code != 200:
        logger.warning("[telegram] post message failed: %s", response.status_code)
        if response.data:
            error_message = str(
                response.data.get("description", response.data.get("error", "unknown"))
            )
        else:
            error_message = response.text or f"HTTP {response.status_code}"
        logger.warning("[telegram] post message failed: %s", error_message)
        return False, error_message, ""
    result = response.data.get("result", {})
    message_id = str(result.get("message_id") or "") if isinstance(result, dict) else ""
    return True, "", message_id


def send_telegram_report(
    report: str,
    telegram_ctx: dict[str, Any],
    *,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Send a truncated report to Telegram. Returns (success, error)."""
    bot_token: str = str(telegram_ctx.get("bot_token") or "")
    chat_id: str = str(telegram_ctx.get("chat_id") or "")
    if not bot_token or not chat_id:
        return False, "Missing bot_token or chat_id"
    reply_to_message_id: str = str(telegram_ctx.get("reply_to_message_id") or "")
    if parse_mode.upper() == "HTML":
        text = truncate_for_telegram_html(report, _MESSAGE_LIMIT, suffix="…")
    else:
        text = truncate(report, _MESSAGE_LIMIT, suffix="…")
    post_success, error, _ = post_telegram_message(
        chat_id,
        text,
        bot_token,
        parse_mode=parse_mode,
        reply_to_message_id=reply_to_message_id,
        reply_markup=reply_markup,
    )
    return (True, "") if post_success else (False, error)
