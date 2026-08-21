"""Telegram turn output with typing indicator and throttled message streaming."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable

from gateway.transports.telegram.poller.client import TelegramBotClient
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.truncation import truncate
from infrastructure.turn_host.status_messages import (
    EMPTY_RESPONSE_MESSAGE,
    initial_status_message,
    normalize_gateway_status,
    status_from_response_label,
    user_facing_error_message,
)
from integrations.telegram.formatting import markdown_to_telegram_html

_LOG_PREVIEW_LIMIT = 500
logger = logging.getLogger("gateway")


def _log_preview(text: str) -> str:
    preview = text.replace("\n", " ").strip()
    if len(preview) > _LOG_PREVIEW_LIMIT:
        return f"{preview[: _LOG_PREVIEW_LIMIT - 3]}..."
    return preview


class TelegramTurnOutput:
    """Stream assistant output back through the Telegram Bot API."""

    def __init__(
        self,
        *,
        client: TelegramBotClient,
        chat_id: str,
        edit_interval_seconds: float = 1.5,
        tool_hooks: object | None = None,
    ) -> None:
        self.tool_hooks = tool_hooks
        # Set per turn by this transport's dispatcher; the turn handler reads it
        # to give tools a cooperative cancel signal on soft timeout or stop.
        self.turn_cancel: threading.Event | None = None
        self._client = client
        self._chat_id = chat_id
        self._edit_interval = edit_interval_seconds
        self._message_id = ""
        self._last_edit = 0.0
        self._lock = threading.Lock()
        self._status_text = initial_status_message()
        self._client.send_chat_action(self._chat_id, "typing")
        ok, _, message_id = self._client.send_message(self._chat_id, self._status_text)
        if ok:
            self._message_id = message_id

    def print(self, message: str = "") -> None:
        if message:
            self._set_status(message)

    def render_response_header(self, label: str) -> None:
        self._set_status(status_from_response_label(label))

    def render_error(self, message: str) -> None:
        # Raw detail to the server log only; the user sees safe generic copy.
        logger.warning("gateway turn error chat=%s: %s", self._chat_id, message)
        self._finalize(user_facing_error_message(message))

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        parts: list[str] = []
        for chunk in chunks:
            parts.append(str(chunk))
            now = time.monotonic()
            if now - self._last_edit >= self._edit_interval:
                self._edit_preview("".join(parts))
        text = "".join(parts)
        if defer_want_me_to_closer:
            return text
        self._finalize(text or EMPTY_RESPONSE_MESSAGE)
        return text

    def set_tool_status(self, status: str) -> None:
        self._set_status(status)

    def finish_streamed_response(self, answer: str) -> None:
        self._finalize(answer or EMPTY_RESPONSE_MESSAGE)

    def _set_status(self, status: str) -> None:
        self._status_text = normalize_gateway_status(status)
        self._client.send_chat_action(self._chat_id, "typing")
        self._edit_preview(self._status_text)

    def _edit_preview(self, preview: str) -> None:
        preview = truncate(preview or self._status_text, MAX_MESSAGE_SIZE, suffix="…")
        with self._lock:
            if not self._message_id:
                # Prior answer was finalized (goal continuation). Open a fresh
                # placeholder instead of editing the finished message.
                ok, _, message_id = self._client.send_message(self._chat_id, preview)
                if ok:
                    self._message_id = message_id
                    self._last_edit = time.monotonic()
                return
            ok, _ = self._client.edit_message_text(self._chat_id, self._message_id, preview)
            if ok:
                self._last_edit = time.monotonic()

    def finalize(self, answer: str) -> None:
        self._finalize(answer)

    def _finalize(self, answer: str) -> None:
        final = truncate(answer, MAX_MESSAGE_SIZE, suffix="…")
        html_final = markdown_to_telegram_html(final)
        if self._message_id and self._edit_final(html_final, final):
            logger.info("outbound chat=%s text=%r", self._chat_id, _log_preview(final))
            # Release the id so the next outer turn cannot overwrite this answer.
            self._message_id = ""
            return
        if self._send_final(html_final, final):
            logger.info("outbound chat=%s text=%r", self._chat_id, _log_preview(final))
            self._message_id = ""

    def _edit_final(self, html_text: str, plain_text: str) -> bool:
        # Render the answer's Markdown as Telegram HTML, falling back to plain answer
        # if the API rejects the markup so a message is never lost to a bad tag.
        ok, _ = self._client.edit_message_text(
            self._chat_id, self._message_id, html_text, parse_mode="HTML"
        )
        if ok:
            return True
        ok, _ = self._client.edit_message_text(self._chat_id, self._message_id, plain_text)
        return ok

    def _send_final(self, html_text: str, plain_text: str) -> bool:
        ok, _, _ = self._client.send_message(self._chat_id, html_text, parse_mode="HTML")
        if ok:
            return True
        ok, _, _ = self._client.send_message(self._chat_id, plain_text)
        return ok
