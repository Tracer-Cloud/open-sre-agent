"""Gateway output sink with typing indicator and throttled message streaming."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable

from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER
from gateway.polling.telegram_poller.client import TelegramBotClient
from gateway.status_messages import (
    initial_status_message,
    normalize_gateway_status,
    status_from_response_label,
)
from integrations.telegram.formatting import markdown_to_telegram_html
from platform.common.truncation import truncate
from platform.notifications.limits import MAX_MESSAGE_SIZE

_LOG_PREVIEW_LIMIT = 500
logger = logging.getLogger("gateway")


def _log_preview(text: str) -> str:
    preview = text.replace("\n", " ").strip()
    if len(preview) > _LOG_PREVIEW_LIMIT:
        return f"{preview[: _LOG_PREVIEW_LIMIT - 3]}..."
    return preview


class GatewayOutputSink:
    """Stream assistant output back through the active messaging transport."""

    def __init__(
        self,
        *,
        client: TelegramBotClient,
        chat_id: str,
        edit_interval_seconds: float = 1.5,
    ) -> None:
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
        hint = ""
        if CREDIT_EXHAUSTED_MARKER in message:
            hint = (
                "\n\nHint: run `opensre auth login <provider>` "
                "to re-authenticate or switch to a different provider."
            )
        self._finalize(f"Error: {message}{hint}")

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        parts: list[str] = []
        for chunk in chunks:
            parts.append(str(chunk))
            now = time.monotonic()
            if now - self._last_edit >= self._edit_interval:
                self._edit_preview("".join(parts))
        text = "".join(parts)
        self._finalize(text or "(no response)")
        return text

    def set_tool_status(self, text: str) -> None:
        self._set_status(text)

    def _set_status(self, text: str) -> None:
        self._status_text = normalize_gateway_status(text)
        self._client.send_chat_action(self._chat_id, "typing")
        self._edit_preview(self._status_text)

    def _edit_preview(self, text: str) -> None:
        if not self._message_id:
            return
        preview = truncate(text or self._status_text, MAX_MESSAGE_SIZE, suffix="…")
        with self._lock:
            ok, _ = self._client.edit_message_text(self._chat_id, self._message_id, preview)
            if ok:
                self._last_edit = time.monotonic()

    def finalize(self, text: str) -> None:
        self._finalize(text)

    def _finalize(self, text: str) -> None:
        final = truncate(text, MAX_MESSAGE_SIZE, suffix="…")
        html_final = markdown_to_telegram_html(final)
        if self._message_id and self._edit_final(html_final, final):
            logger.info("outbound chat=%s text=%r", self._chat_id, _log_preview(final))
            return
        if self._send_final(html_final, final):
            logger.info("outbound chat=%s text=%r", self._chat_id, _log_preview(final))

    def _edit_final(self, html_text: str, plain_text: str) -> bool:
        # Render the answer's Markdown as Telegram HTML, falling back to plain text
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
