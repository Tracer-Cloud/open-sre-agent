"""Slack output sink: placeholder status message edited in place, final answer in-thread."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable

from gateway.runtime.status_messages import (
    initial_status_message,
    normalize_gateway_status,
    status_from_response_label,
)
from gateway.slack.client import SlackMessagingClient
from platform.common.truncation import truncate

# Slack rejects chat.postMessage text above this length with msg_too_long.
SLACK_MAX_MESSAGE_CHARS = 40_000

logger = logging.getLogger("gateway")


class SlackOutputSink:
    """Stream assistant output back to the triggering Slack thread.

    Posts one status placeholder as a thread reply, edits it in place while the
    turn runs (throttled), and replaces it with the final answer.
    """

    def __init__(
        self,
        *,
        client: SlackMessagingClient,
        channel_id: str,
        thread_ts: str,
        update_interval_seconds: float = 1.5,
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._update_interval = update_interval_seconds
        self._last_update = 0.0
        self._lock = threading.Lock()
        self._message_ts = client.post_message(
            channel=channel_id,
            text=initial_status_message(),
            thread_ts=thread_ts,
        )

    def print(self, message: str = "") -> None:
        if message:
            self._set_status(message)

    def render_response_header(self, label: str) -> None:
        self._set_status(status_from_response_label(label))

    def render_error(self, message: str) -> None:
        self._finalize(f"Error: {message}")

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
            if now - self._last_update >= self._update_interval:
                self._edit_preview("".join(parts))
        text = "".join(parts)
        self._finalize(text or "(no response)")
        return text

    def set_tool_status(self, text: str) -> None:
        self._set_status(text)

    def finalize(self, text: str) -> None:
        self._finalize(text)

    def _set_status(self, text: str) -> None:
        self._edit_preview(normalize_gateway_status(text))

    def _edit_preview(self, text: str) -> None:
        if not self._message_ts:
            return
        preview = truncate(text, SLACK_MAX_MESSAGE_CHARS, suffix="…")
        with self._lock:
            if self._client.update_message(
                channel=self._channel_id, ts=self._message_ts, text=preview
            ):
                self._last_update = time.monotonic()

    def _finalize(self, text: str) -> None:
        final = truncate(text, SLACK_MAX_MESSAGE_CHARS, suffix="…")
        with self._lock:
            delivered = self._message_ts is not None and self._client.update_message(
                channel=self._channel_id, ts=self._message_ts, text=final
            )
            if not delivered:
                delivered = (
                    self._client.post_message(
                        channel=self._channel_id, text=final, thread_ts=self._thread_ts
                    )
                    is not None
                )
        if delivered:
            logger.info(
                "outbound channel=%s chars=%d",
                self._channel_id,
                len(final),
            )
