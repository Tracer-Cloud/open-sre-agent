"""Discord gateway output sink."""

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
from gateway.transports.discord.client import (
    edit_message,
    edit_message_with_components,
    send_message,
    send_message_with_components,
    split_discord_content,
)
from gateway.transports.discord.feedback import feedback_components

logger = logging.getLogger("gateway")


class DiscordOutputSink:
    """Stream assistant output back to a Discord channel or thread."""

    redacts_raw_tool_output = True

    def __init__(
        self,
        *,
        bot_token: str,
        channel_id: str,
        edit_interval_seconds: float = 2.0,
        tool_hooks: object | None = None,
    ) -> None:
        self.tool_hooks = tool_hooks
        # Set per turn by this transport's dispatcher; the turn handler reads it
        # to give tools a cooperative cancel signal on soft timeout or stop.
        self.turn_cancel: threading.Event | None = None
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._edit_interval = edit_interval_seconds
        self._lock = threading.RLock()
        self._last_edit = 0.0
        self._message_id = send_message(
            channel_id=channel_id,
            content=f"*{initial_status_message()}*",
            bot_token=bot_token,
        )

    def print(self, message: str = "") -> None:
        if message:
            self._set_status(message)

    def render_response_header(self, label: str) -> None:
        self._set_status(status_from_response_label(label))

    def render_error(self, message: str) -> None:
        logger.warning("gateway turn error channel=%s: %s", self._channel_id, message)
        self.finalize(user_facing_error_message(message))

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
            combined = "".join(parts)
            now = time.monotonic()
            if now - self._last_edit >= self._edit_interval:
                self._edit_preview(combined)
        text = "".join(parts)
        if defer_want_me_to_closer:
            return text
        self.finalize(text or EMPTY_RESPONSE_MESSAGE)
        return text

    def set_tool_status(self, text: str, *, call_id: str | None = None) -> None:
        _ = call_id  # Discord has no timeline
        self._set_status(text)

    def end_tool_status(self, *, failed: bool, call_id: str | None = None) -> None:
        """No-op: Discord has no timeline."""
        _ = (failed, call_id)

    def leave_tool_status_open(
        self, *, call_id: str | None = None, title: str | None = None
    ) -> None:
        """No-op: Discord has no timeline."""
        _ = (call_id, title)
        _ = call_id

    def finish_streamed_response(self, text: str) -> None:
        self.finalize(text)

    def finalize(self, text: str, *, failed: bool = False) -> None:
        _ = failed  # Discord shows no turn-level success/failure state
        body = (text or EMPTY_RESPONSE_MESSAGE).strip()
        chunks = split_discord_content(body)
        if not chunks:
            return
        with self._lock:
            # Release the placeholder so a later outer-goal turn posts a new
            # message instead of overwriting the answer already delivered.
            message_id = self._message_id
            self._message_id = ""
            if not message_id:
                # Continuation turn: nothing to edit, so deliver fresh messages.
                for extra in chunks[:-1]:
                    send_message(
                        channel_id=self._channel_id,
                        content=extra,
                        bot_token=self._bot_token,
                    )
                send_message_with_components(
                    channel_id=self._channel_id,
                    content=chunks[-1],
                    components=feedback_components(),
                    bot_token=self._bot_token,
                )
                return
            if len(chunks) == 1:
                edit_message_with_components(
                    channel_id=self._channel_id,
                    message_id=message_id,
                    content=chunks[0],
                    components=feedback_components(),
                    bot_token=self._bot_token,
                )
                return
            edit_message(
                channel_id=self._channel_id,
                message_id=message_id,
                content=chunks[0],
                bot_token=self._bot_token,
            )
            for extra in chunks[1:-1]:
                send_message(
                    channel_id=self._channel_id,
                    content=extra,
                    bot_token=self._bot_token,
                )
            send_message_with_components(
                channel_id=self._channel_id,
                content=chunks[-1],
                components=feedback_components(),
                bot_token=self._bot_token,
            )

    def _set_status(self, text: str) -> None:
        status = normalize_gateway_status(text)
        self._edit_preview(f"*{status}*")

    def _edit_preview(self, text: str) -> None:
        with self._lock:
            if not self._message_id:
                self._message_id = send_message(
                    channel_id=self._channel_id,
                    content=text[:2000],
                    bot_token=self._bot_token,
                )
                return
            now = time.monotonic()
            if now - self._last_edit < self._edit_interval:
                return
            if edit_message(
                channel_id=self._channel_id,
                message_id=self._message_id,
                content=text[:2000],
                bot_token=self._bot_token,
            ):
                self._last_edit = now
