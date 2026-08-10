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

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from config.constants.agent_identity import agent_name
from core.execution import ToolExecutionHooks
from gateway.core.runtime.status_messages import (
    EMPTY_RESPONSE_MESSAGE,
    TOOL_STATUS_PREFIX,
    initial_status_message,
    normalize_gateway_status,
    status_from_response_label,
    user_facing_error_message,
)
from gateway.transports.slack.client import Blocks, SlackMessagingClient
from gateway.transports.slack.feedback import feedback_block
from integrations.slack.formatting import markdown_to_slack_mrkdwn
from platform.common.duration import format_duration
from platform.common.truncation import truncate

# Slack rejects chat.postMessage text above this length with msg_too_long.
SLACK_MAX_MESSAGE_CHARS = 40_000
# Block Kit markdown blocks cap at 12k chars; longer answers fall back to
# mrkdwn text, which Slack accepts up to SLACK_MAX_MESSAGE_CHARS.
SLACK_MAX_MARKDOWN_BLOCK_CHARS = 12_000
# Cap on a single task_update chunk — the whole chunk, not just its title, and
# an over-long one fails the entire chat.appendStream call rather than dropping
# that one row.
SLACK_MAX_TASK_UPDATE_CHARS = 256
# Provenance footer: says the answer came from a model, and that the reader
# still owns the facts. Kept to one clause — it sits under every reply, so a
# long caveat is one people stop reading.
AI_DISCLOSURE = "AI-generated — verify key details"

logger = logging.getLogger("gateway")


def ai_disclosure_footer_block(*, elapsed_text: str | None = None) -> dict[str, object]:
    """The provenance footer shown under model-generated text.

    Shared by the turn-reply footer (which has an elapsed-time reading off the
    turn's own clock) and the detached-investigation report (which has no
    single turn to time against) — same disclosure, an optional duration.
    """
    suffix = f" · {elapsed_text}" if elapsed_text else ""
    text = f"{agent_name()} · {AI_DISCLOSURE}{suffix}"
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": text}],
    }


class SlackOutputSink:
    """Stream assistant output back to the triggering Slack thread."""

    redacts_raw_tool_output = True

    def __init__(
        self,
        *,
        client: SlackMessagingClient,
        channel_id: str,
        thread_ts: str,
        team_id: str,
        user_id: str,
        update_interval_seconds: float = 3.0,
        tool_hooks: ToolExecutionHooks | None = None,
    ) -> None:
        # Per-turn tool-execution hooks (e.g. the Block Kit approval gate),
        # read duck-typed by GatewayTurnHandler when building the agent.
        self.tool_hooks = tool_hooks
        # Set per turn by this transport's dispatcher; the turn handler reads it
        # to give tools a cooperative cancel signal on soft timeout or stop.
        self.turn_cancel: threading.Event | None = None
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
            team_id=team_id,
            user_id=user_id,
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
        self._finalize(user_facing_error_message(message), failed=True)

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
            text_chunk = str(chunk)
            parts.append(text_chunk)
            with self._lock:
                if self._turn_stream.append_text(text_chunk):
                    continue
            now = time.monotonic()
            if now - self._last_update >= self._update_interval:
                self._edit_preview("".join(parts))
        text = "".join(parts)
        if defer_want_me_to_closer:
            # Preview may show a drifted closer; finish_streamed_response
            # publishes the canonical rewrite after gather normalize.
            return text
        self._finalize(text or EMPTY_RESPONSE_MESSAGE)
        return text

    def set_tool_status(self, text: str, *, call_id: str | None = None) -> None:
        self._set_status(text, call_id=call_id)

    def end_tool_status(self, *, failed: bool, call_id: str | None = None) -> None:
        with self._lock:
            if self._turn_stream.started:
                self._turn_stream.close_task(failed=failed, call_id=call_id)

    def leave_tool_status_open(
        self, *, call_id: str | None = None, title: str | None = None
    ) -> None:
        with self._lock:
            if self._turn_stream.started:
                self._turn_stream.release_task(call_id=call_id, title=title)

    def finalize(self, text: str, *, failed: bool = False) -> None:
        self._finalize(text, failed=failed)

    def finish_streamed_response(self, text: str) -> None:
        self._finalize(text or EMPTY_RESPONSE_MESSAGE)

    def _set_status(self, text: str, *, call_id: str | None = None) -> None:
        status = normalize_gateway_status(text)
        with self._lock:
            if self._turn_stream.note_task(status, call_id=call_id):
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

    def _finalize(self, text: str, *, failed: bool = False) -> None:
        with self._lock:
            if self._turn_stream.is_open:
                if self._turn_stream.finish(text, blocks=self._closing_blocks(), failed=failed):
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
            elif self._turn_stream.closed:
                # Prior stopStream (outer goal continuation, or a raced finalize).
                # Open a fresh stream so later-turn text is not treated as already
                # delivered; if start fails, fall through to classic post.
                if self._turn_stream.ensure_started_for_continuation() and self._turn_stream.finish(
                    text, blocks=self._closing_blocks(), failed=failed
                ):
                    logger.info(
                        "outbound channel=%s thread_ts=%s mode=stream-continuation chars=%d",
                        self._channel_id,
                        self._thread_ts,
                        len(text),
                    )
                    return
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
        elapsed = format_duration(time.monotonic() - self._started_at)
        return [ai_disclosure_footer_block(elapsed_text=elapsed), feedback_block()]


@dataclass(frozen=True)
class _TimelineTask:
    """One timeline row, held open until its tool reports an outcome.

    Carries no call id: which tool a row belongs to is the key it is filed
    under, so a copy on the row would be a second answer that could disagree.
    """

    task_id: str
    title: str


def _task_update_chunk(*, task_id: str, title: str, status: str) -> dict[str, object]:
    """Build a ``task_update`` chunk that fits Slack's per-chunk limit.

    A tool that echoes its own arguments into a status line would otherwise take
    the rest of the turn's output down with it. The budget is measured on the
    encoded chunk rather than guessed, because the fixed keys and JSON escaping
    both spend from the same 256 the title does.
    """
    chunk: dict[str, object] = {
        "type": "task_update",
        "id": task_id,
        "title": title,
        "status": status,
    }
    overflow = len(json.dumps(chunk, ensure_ascii=False)) - SLACK_MAX_TASK_UPDATE_CHARS
    if overflow > 0:
        # Dropping N characters frees at least N encoded ones, so one pass is enough.
        chunk["title"] = truncate(title, max(len(title) - overflow, 1), suffix="…")
    return chunk


class _TurnStream:
    """One turn's streamed Slack message (``chat.startStream`` lifecycle).

    Started lazily on the first tool status or answer chunk. Tool statuses
    become timeline ``task_update`` chunks, each row closing on the outcome of
    the tool call that opened it; answer text streams as throttled
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
        team_id: str,
        user_id: str,
        update_interval_seconds: float,
        on_started: Callable[[], None],
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._team_id = team_id
        self._user_id = user_id
        self._update_interval = update_interval_seconds
        self._on_started = on_started
        self._ts: str | None = None
        # Successful stopStream — next ensure_started may open a fresh stream.
        self._closed = False
        # chat.startStream failed — stay on the placeholder path; do not re-probe.
        self._unavailable = False
        self._broken = False
        self._task_seq = 0
        # Tool rows, keyed by call id and closed by their own end event. A batch
        # of tool calls has every row open at once, so one slot cannot hold them.
        self._open_tool_tasks: dict[str, _TimelineTask] = {}
        # The at-most-one row opened by status text that is not a tool call. It
        # has no closing event, so the next row (or the answer) closes it.
        self._open_untracked: _TimelineTask | None = None
        self._sent_text = ""
        self._pending_parts: list[str] = []
        self._last_flush = 0.0

    def _joined_pending(self) -> str:
        """Materialize buffered answer chunks (join once per flush)."""
        return "".join(self._pending_parts)

    @property
    def started(self) -> bool:
        return self._ts is not None

    @property
    def is_open(self) -> bool:
        """True while a stream is live and can still accept finish/append."""
        return (
            self._ts is not None and not self._closed and not self._broken and not self._unavailable
        )

    @property
    def dead(self) -> bool:
        """True after a successful stop (continuation may reopen) or a failed start."""
        return self._closed or self._unavailable

    @property
    def closed(self) -> bool:
        """True after a successful ``stopStream`` — safe to open a continuation stream."""
        return self._closed

    def ensure_started_for_continuation(self) -> bool:
        """Reset a finished stream and open a new one for the next outer turn."""
        return self._ensure_started()

    def note_task(self, title: str, *, call_id: str | None = None) -> bool:
        """Open an in-progress timeline row for ``title``.

        A tool row (``call_id`` set) stays open until its own ``tool_end``
        closes it. The runtime emits *every* start in a batch before any end
        (``core/agent/react_loop.py``), so closing the previous row here would
        tick off N-1 tools before they had run. A row opened by plain status
        text has no closing event, so the next one still closes it.
        """
        if not self._ensure_started():
            return False
        chunks: list[dict[str, object]] = []
        if call_id is None and self._open_untracked is not None:
            chunks.append(self._close_chunk(self._open_untracked, failed=False))
            self._open_untracked = None
        self._task_seq += 1
        task_id = f"task-{self._task_seq}"
        chunks.append(_task_update_chunk(task_id=task_id, title=title, status="in_progress"))
        if not self._append(chunks):
            return False
        task = _TimelineTask(task_id=task_id, title=title)
        if call_id is None:
            self._open_untracked = task
        else:
            self._open_tool_tasks[call_id] = task
        return True

    def close_task(self, *, failed: bool, call_id: str | None = None) -> bool:
        """Close the row ``call_id`` opened, with the outcome its tool had.

        An unknown ``call_id`` closes nothing: under a batch every other row
        belongs to a different tool, so closing "whatever is open" would put
        this tool's outcome against someone else's work.
        """
        if call_id is None:
            task, self._open_untracked = self._open_untracked, None
        else:
            task = self._open_tool_tasks.pop(call_id, None)
        if task is None:
            return True
        return self._append([self._close_chunk(task, failed=failed)])

    def release_task(self, *, call_id: str | None = None, title: str | None = None) -> None:
        """Release the task without emitting a completion chunk.

        For tools that hand off to background runs: removes from tracking so
        finish() won't close it. Slack has no status value for "still running
        elsewhere" — a row left ``in_progress`` past the turn's own stream end
        renders as stale rather than active — so an optional ``title`` posts
        one last still-``in_progress`` update naming that hand-off, best-effort
        (the row is released from tracking either way).
        """
        task = self._open_untracked if call_id is None else self._open_tool_tasks.get(call_id)
        if title is not None and task is not None:
            self._append(
                [_task_update_chunk(task_id=task.task_id, title=title, status="in_progress")]
            )
        if call_id is None:
            self._open_untracked = None
        else:
            self._open_tool_tasks.pop(call_id, None)

    def append_text(self, chunk: str) -> bool:
        """Buffer an answer chunk; flush on the update interval."""
        if self._broken or not self._ensure_started():
            return False
        self._pending_parts.append(chunk)
        if time.monotonic() - self._last_flush >= self._update_interval:
            self._flush_text()
        # Buffered content is delivered by finish() even if this flush failed.
        return not self._broken

    def finish(self, full_text: str, *, blocks: Blocks | None, failed: bool = False) -> bool:
        """Deliver any remaining text and stop the stream.

        Returns whether the streamed message contains the complete answer;
        on False the caller re-delivers ``full_text`` through the fallback
        path (the stream, if still open server-side, times out on its own).
        """
        if self._ts is None:
            return False
        if self._closed:
            # Already finished once (e.g. a timeout finalize raced the answer);
            # the streamed message stands as delivered.
            return True
        streamed = self._sent_text + self._joined_pending()
        if full_text.startswith(streamed):
            self._pending_parts.append(full_text[len(streamed) :])
        elif full_text != streamed:
            # A finalize with unrelated text (error copy, timeout notice)
            # lands after whatever partial answer already streamed.
            if streamed:
                self._pending_parts.append("\n\n")
            self._pending_parts.append(full_text)
        self._flush_text(include_task_close=True, task_failed=failed)
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
        self._closed = True
        return True

    def _ensure_started(self) -> bool:
        if self._broken or self._unavailable:
            return False
        if self._closed:
            # Prior response stopped (outer goal continuation). Open a fresh
            # stream instead of treating later appends as already delivered.
            self._reset_for_continuation()
        if self._ts is not None:
            return True
        ts = self._client.start_stream(
            channel=self._channel_id,
            thread_ts=self._thread_ts,
            recipient_team_id=self._team_id,
            recipient_user_id=self._user_id,
        )
        if ts is None:
            self._unavailable = True
            return False
        self._ts = ts
        self._last_flush = time.monotonic()
        self._on_started()
        return True

    def _reset_for_continuation(self) -> None:
        """Clear a finished stream so the next outer turn can start another."""
        self._ts = None
        self._closed = False
        self._broken = False
        self._task_seq = 0
        self._open_tool_tasks = {}
        self._open_untracked = None
        self._sent_text = ""
        self._pending_parts.clear()
        self._last_flush = 0.0

    def _flush_text(self, *, include_task_close: bool = False, task_failed: bool = False) -> None:
        chunks: list[dict[str, object]] = []
        pending = self._joined_pending()
        if include_task_close:
            # The turn is over: nothing may be left spinning, including a tool
            # row whose end event never arrived.
            chunks.extend(self._close_all_open_task_chunks(failed=task_failed))
        elif pending and self._open_untracked is not None:
            # Answer text starting retires the status row it replaces. Tool rows
            # are left alone — mid-turn they may still be running.
            chunks.append(self._close_chunk(self._open_untracked, failed=False))
            self._open_untracked = None
        if pending:
            budget = SLACK_MAX_MARKDOWN_BLOCK_CHARS - len(self._sent_text)
            text = truncate(pending, max(budget, 1), suffix="…")
            chunks.append({"type": "markdown_text", "text": text})
            self._sent_text += pending
            self._pending_parts.clear()
        if chunks:
            self._append(chunks)

    def _close_all_open_task_chunks(self, *, failed: bool = False) -> list[dict[str, object]]:
        """Retire every row still open, oldest first."""
        tasks = list(self._open_tool_tasks.values())
        self._open_tool_tasks.clear()
        if self._open_untracked is not None:
            tasks.append(self._open_untracked)
            self._open_untracked = None
        return [self._close_chunk(task, failed=failed) for task in tasks]

    def _close_chunk(self, task: _TimelineTask, *, failed: bool) -> dict[str, object]:
        # A finished row still reading "⏳ …" contradicts the ✓/✗ beside it.
        return _task_update_chunk(
            task_id=task.task_id,
            title=task.title.removeprefix(TOOL_STATUS_PREFIX),
            status="error" if failed else "complete",
        )

    def _append(self, chunks: list[dict[str, object]]) -> bool:
        if self._ts is None:
            return False
        if self._client.append_stream(channel=self._channel_id, ts=self._ts, chunks=chunks):
            self._last_flush = time.monotonic()
            return True
        self._broken = True
        return False


def _as_status_line(text: str) -> str:
    """Render an in-progress status as one italic mrkdwn line.

    Mirrors the "is thinking…" affordance in Claude Tag / Slack assistant
    threads: progress reads as muted meta-text, clearly distinct from the
    final answer that replaces it.
    """
    line = " ".join(text.split())
    return f"_{line}_" if line else line
