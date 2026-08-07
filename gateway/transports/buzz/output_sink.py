"""Buzz gateway output sink with throttled status editing.

Unlike Telegram, Buzz content is already markdown (Desktop renders it
directly), so no HTML transform is needed on the way out. Status updates edit
one message in place via ``buzz messages edit`` (a stored event, confirmed
safe for repeated edits — see gateway/transports/buzz/__init__.py), matching
Telegram's/Discord's UX rather than posting a new message per update.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable

from gateway.core.runtime.status_messages import (
    EMPTY_RESPONSE_MESSAGE,
    initial_status_message,
    normalize_gateway_status,
    status_from_response_label,
    user_facing_error_message,
)
from integrations.buzz.client import BuzzClient
from platform.common.truncation import truncate
from platform.notifications.limits import MAX_MESSAGE_SIZE

_LOG_PREVIEW_LIMIT = 500
logger = logging.getLogger("gateway")


def _log_preview(text: str) -> str:
    preview = text.replace("\n", " ").strip()
    if len(preview) > _LOG_PREVIEW_LIMIT:
        return f"{preview[: _LOG_PREVIEW_LIMIT - 3]}..."
    return preview


class BuzzOutputSink:
    """Stream assistant output back through the Buzz channel."""

    def __init__(
        self,
        *,
        client: BuzzClient,
        channel_id: str,
        edit_interval_seconds: float = 1.5,
        tool_hooks: object | None = None,
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._edit_interval = edit_interval_seconds
        self.tool_hooks = tool_hooks
        self._event_id = ""
        self._last_edit = 0.0
        self._lock = threading.Lock()
        self._status_text = initial_status_message()
        result = self._client.send_message(channel=self._channel_id, content=self._status_text)
        if result["success"]:
            self._event_id = result["event_id"]

    def print(self, message: str = "") -> None:
        if message:
            self._set_status(message)

    def render_response_header(self, label: str) -> None:
        self._set_status(status_from_response_label(label))

    def render_error(self, message: str) -> None:
        # Raw detail to the server log only; the user sees safe generic copy.
        logger.warning("gateway turn error channel=%s: %s", self._channel_id, message)
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

    def set_tool_status(self, text: str) -> None:
        self._set_status(text)

    def finish_streamed_response(self, text: str) -> None:
        self._finalize(text or EMPTY_RESPONSE_MESSAGE)

    def _set_status(self, text: str) -> None:
        self._status_text = normalize_gateway_status(text)
        self._edit_preview(self._status_text)

    def _edit_preview(self, text: str) -> None:
        if not self._event_id:
            return
        preview = truncate(text or self._status_text, MAX_MESSAGE_SIZE, suffix="…")
        with self._lock:
            result = self._client.edit_message(event_id=self._event_id, content=preview)
            if result["success"]:
                self._last_edit = time.monotonic()

    def finalize(self, text: str) -> None:
        self._finalize(text)

    def _finalize(self, text: str) -> None:
        final = truncate(text, MAX_MESSAGE_SIZE, suffix="…")
        if self._event_id and self._edit_final(final):
            logger.info("outbound channel=%s text=%r", self._channel_id, _log_preview(final))
            return
        if self._send_final(final):
            logger.info("outbound channel=%s text=%r", self._channel_id, _log_preview(final))

    def _edit_final(self, text: str) -> bool:
        result = self._client.edit_message(event_id=self._event_id, content=text)
        return bool(result["success"])

    def _send_final(self, text: str) -> bool:
        result = self._client.send_message(channel=self._channel_id, content=text)
        return bool(result["success"])


__all__ = ["BuzzOutputSink"]
