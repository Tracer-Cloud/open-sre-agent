"""Slack output sink: streamed timeline reply with placeholder-edit fallback.

Preferred delivery is Slack's streaming surface (``chat.startStream`` →
``chat.appendStream`` → ``chat.stopStream``): tool progress renders as
timeline task cards and the answer streams as native markdown, like Claude
Tag. When streaming is unavailable (feature-gated workspace, old plan, API
error) the sink falls back to the classic flow — one status placeholder
posted in-thread, edited in place while the turn runs, replaced by the final
answer.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable

from core.execution import ToolExecutionHooks
from gateway.runtime.status_messages import (
    EMPTY_RESPONSE_MESSAGE,
    initial_status_message,
    normalize_gateway_status,
    status_from_response_label,
    user_facing_error_message,
)
from gateway.slack.client import Blocks, SlackMessagingClient
from gateway.slack.feedback import feedback_block
from integrations.slack.formatting import markdown_to_slack_mrkdwn
from platform.common.truncation import truncate

# Slack rejects chat.postMessage text above this length with msg_too_long.
SLACK_MAX_MESSAGE_CHARS = 40_000
# Block Kit markdown blocks cap at 12k chars; longer answers fall back to
# mrkdwn text, which Slack accepts up to SLACK_MAX_MESSAGE_CHARS.
SLACK_MAX_MARKDOWN_BLOCK_CHARS = 12_000

logger = logging.getLogger("gateway")


class SlackOutputSink:
    """Stream assistant output back to the triggering Slack thread."""

    def __init__(
        self,
        *,
        client: SlackMessagingClient,
        channel_id: str,
        thread_ts: str,
        update_interval_seconds: float = 3.0,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> None:
        # Per-turn tool-execution hooks (e.g. the Block Kit approval gate),
        # read duck-typed by GatewayTurnHandler when building the agent.
        self.tool_hooks = tool_hooks
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._update_interval = update_interval_seconds
        self._last_update = 0.0
        self._started_at = time.monotonic()
        # RLock: the turn stream's on-start callback deletes the placeholder
        # from inside an already-locked status/stream call.
        self._lock = threading.RLock()
        self._turn_stream = _TurnStream(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            update_interval_seconds=update_interval_seconds,
            on_started=self._drop_placeholder,
        )
        self._message_ts = client.post_message(
            channel=channel_id,
            text=_as_status_line(initial_status_message()),
            thread_ts=thread_ts,
        )
        if self._message_ts is None:
            logger.warning(
                "[slack-sink] placeholder post FAILED channel=%s thread_ts=%s; "
                "final answer will be posted as a new message",
                channel_id,
                thread_ts,
            )

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
    ) -> str:
        _ = (label, suppress_if_starts_with)
        parts: list[str] = []
        for chunk in chunks:
            text_chunk = str(chunk)
            parts.append(text_chunk)
            with self._lock:
                if self._turn_stream.append_text(text_chunk):
                    continue
            now = time.monotonic()
            if now - self._last_update >= self._update_interval:
                self._edit_preview("".join(parts))
        text = "".join(parts)
        self._finalize(text or EMPTY_RESPONSE_MESSAGE)
        return text

    def set_tool_status(self, text: str) -> None:
        self._set_status(text)

    def finalize(self, text: str) -> None:
        self._finalize(text)

    def _set_status(self, text: str) -> None:
        status = normalize_gateway_status(text)
        with self._lock:
            if self._turn_stream.note_task(status):
                return
        self._edit_preview(_as_status_line(status))

    def _drop_placeholder(self) -> None:
        """The streamed message replaces the placeholder — remove it."""
        with self._lock:
            ts = self._message_ts
            self._message_ts = None
        if ts:
            self._client.delete_message(channel=self._channel_id, ts=ts)

    def _edit_preview(self, text: str) -> None:
        if not self._message_ts:
            return
        preview = truncate(text, SLACK_MAX_MESSAGE_CHARS, suffix="…")
        with self._lock:
            if self._message_ts and self._client.update_message(
                channel=self._channel_id, ts=self._message_ts, text=preview
            ):
                self._last_update = time.monotonic()

    def _finalize(self, text: str) -> None:
        with self._lock:
            if self._turn_stream.started:
                if self._turn_stream.finish(text, blocks=self._closing_blocks()):
                    logger.info(
                        "outbound channel=%s thread_ts=%s mode=stream chars=%d",
                        self._channel_id,
                        self._thread_ts,
                        len(text),
                    )
                    return
                # Stream broke mid-turn: deliver the full answer the classic way.
                logger.warning(
                    "[slack-sink] stream delivery failed channel=%s thread_ts=%s; "
                    "falling back to a plain message",
                    self._channel_id,
                    self._thread_ts,
                )
        final = truncate(markdown_to_slack_mrkdwn(text), SLACK_MAX_MESSAGE_CHARS, suffix="…")
        blocks = self._final_blocks(text)
        mode = "edit"
        with self._lock:
            delivered = self._message_ts is not None and self._client.update_message(
                channel=self._channel_id, ts=self._message_ts, text=final, blocks=blocks
            )
            if not delivered:
                mode = "new-message"
                delivered = (
                    self._client.post_message(
                        channel=self._channel_id,
                        text=final,
                        thread_ts=self._thread_ts,
                        blocks=blocks,
                    )
                    is not None
                )
        if delivered:
            logger.info(
                "outbound channel=%s thread_ts=%s mode=%s chars=%d",
                self._channel_id,
                self._thread_ts,
                mode,
                len(final),
            )
        else:
            # Both the in-place edit and the fresh post failed: the user is left
            # staring at the "Digging in…" placeholder with no answer.
            logger.error(
                "[slack-sink] DELIVERY FAILED channel=%s thread_ts=%s chars=%d "
                "(both update and post rejected)",
                self._channel_id,
                self._thread_ts,
                len(final),
            )

    def _final_blocks(self, text: str) -> Blocks | None:
        """Compose the final reply: a ``markdown`` block + a context footer.

        Slack built the markdown block for LLM output: standard markdown
        (headers, tables, fenced code) renders natively instead of being
        mangled through mrkdwn. The context footer is the Claude-Tag-style
        provenance line (who answered, how long it took) rendered in Slack's
        muted small type. Answers over the block's 12k-char limit stay
        text-only; the mrkdwn text is always sent alongside as the
        notification/fallback rendering.
        """
        body = text.strip()
        if not body or len(body) > SLACK_MAX_MARKDOWN_BLOCK_CHARS:
            return None
        return [{"type": "markdown", "text": body}, *self._closing_blocks()]

    def _closing_blocks(self) -> list[dict[str, object]]:
        """Provenance footer + 👍/👎 feedback buttons, on every final reply."""
        return [self._footer_block(), feedback_block()]

    def _footer_block(self) -> dict[str, object]:
        return {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": self._footer_text()}],
        }

    def _footer_text(self) -> str:
        return f"OpenSRE · AI-generated · {_format_duration(time.monotonic() - self._started_at)}"


class _TurnStream:
    """One turn's streamed Slack message (``chat.startStream`` lifecycle).

    Started lazily on the first tool status or answer chunk. Tool statuses
    become timeline ``task_update`` chunks (the previous task flips to
    ``complete`` when the next one starts); answer text streams as throttled
    ``markdown_text`` chunks. A start failure marks the stream dead for the
    turn and the sink stays on the placeholder path; an append failure after
    a successful start marks it broken and the sink re-delivers in full.
    """

    def __init__(
        self,
        *,
        client: SlackMessagingClient,
        channel_id: str,
        thread_ts: str,
        update_interval_seconds: float,
        on_started: Callable[[], None],
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._update_interval = update_interval_seconds
        self._on_started = on_started
        self._ts: str | None = None
        self._dead = False
        self._broken = False
        self._task_seq = 0
        self._open_task: tuple[str, str] | None = None  # (id, title)
        self._sent_text = ""
        self._pending_text = ""
        self._last_flush = 0.0

    @property
    def started(self) -> bool:
        return self._ts is not None

    def note_task(self, title: str) -> bool:
        """Show ``title`` as the new in-progress timeline task."""
        if not self._ensure_started():
            return False
        chunks: list[dict[str, object]] = list(self._close_open_task_chunks())
        self._task_seq += 1
        task_id = f"task-{self._task_seq}"
        chunks.append(
            {"type": "task_update", "id": task_id, "title": title, "status": "in_progress"}
        )
        if not self._append(chunks):
            return False
        self._open_task = (task_id, title)
        return True

    def append_text(self, chunk: str) -> bool:
        """Buffer an answer chunk; flush on the update interval."""
        if self._broken or not self._ensure_started():
            return False
        self._pending_text += chunk
        if time.monotonic() - self._last_flush >= self._update_interval:
            self._flush_text()
        # Buffered content is delivered by finish() even if this flush failed.
        return not self._broken

    def finish(self, full_text: str, *, blocks: Blocks | None) -> bool:
        """Deliver any remaining text and stop the stream.

        Returns whether the streamed message contains the complete answer;
        on False the caller re-delivers ``full_text`` through the fallback
        path (the stream, if still open server-side, times out on its own).
        """
        if self._ts is None:
            return False
        if self._dead:
            # Already finished once (e.g. a timeout finalize raced the answer);
            # the streamed message stands as delivered.
            return True
        streamed = self._sent_text + self._pending_text
        if full_text.startswith(streamed):
            self._pending_text += full_text[len(streamed) :]
        elif full_text != streamed:
            # A finalize with unrelated text (error copy, timeout notice)
            # lands after whatever partial answer already streamed.
            self._pending_text += ("\n\n" if streamed else "") + full_text
        self._flush_text(include_task_close=True)
        stopped = self._client.stop_stream(channel=self._channel_id, ts=self._ts, blocks=blocks)
        if self._broken:
            return False
        if not stopped:
            # Content is fully appended; a failed stop only leaves the
            # streaming indicator until Slack expires it. Don't re-post.
            logger.warning(
                "[slack-sink] chat.stopStream failed channel=%s ts=%s",
                self._channel_id,
                self._ts,
            )
        self._dead = True
        return True

    def _ensure_started(self) -> bool:
        if self._dead or self._broken:
            return False
        if self._ts is not None:
            return True
        ts = self._client.start_stream(channel=self._channel_id, thread_ts=self._thread_ts)
        if ts is None:
            self._dead = True
            return False
        self._ts = ts
        self._last_flush = time.monotonic()
        self._on_started()
        return True

    def _flush_text(self, *, include_task_close: bool = False) -> None:
        chunks: list[dict[str, object]] = []
        if include_task_close or self._pending_text:
            # Answer text starting (or the turn ending) closes the open task.
            chunks.extend(self._close_open_task_chunks())
        if self._pending_text:
            budget = SLACK_MAX_MARKDOWN_BLOCK_CHARS - len(self._sent_text)
            text = truncate(self._pending_text, max(budget, 1), suffix="…")
            chunks.append({"type": "markdown_text", "text": text})
            self._sent_text += self._pending_text
            self._pending_text = ""
        if chunks:
            self._append(chunks)

    def _close_open_task_chunks(self) -> list[dict[str, object]]:
        if self._open_task is None:
            return []
        (task_id, title), self._open_task = self._open_task, None
        return [{"type": "task_update", "id": task_id, "title": title, "status": "complete"}]

    def _append(self, chunks: list[dict[str, object]]) -> bool:
        if self._ts is None:
            return False
        if self._client.append_stream(channel=self._channel_id, ts=self._ts, chunks=chunks):
            self._last_flush = time.monotonic()
            return True
        self._broken = True
        return False


def _format_duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    if whole < 60:
        return f"{whole}s"
    return f"{whole // 60}m {whole % 60:02d}s"


def _as_status_line(text: str) -> str:
    """Render an in-progress status as one italic mrkdwn line.

    Mirrors the "is thinking…" affordance in Claude Tag / Slack assistant
    threads: progress reads as muted meta-text, clearly distinct from the
    final answer that replaces it.
    """
    line = " ".join(text.split())
    return f"_{line}_" if line else line
