from __future__ import annotations

from gateway.slack.output_sink import SLACK_MAX_MESSAGE_CHARS, SlackOutputSink


class _FakeMessagingClient:
    """Records posts/updates; per-instance switches simulate API failures."""

    def __init__(self, *, post_ok: bool = True, update_ok: bool = True) -> None:
        self.post_ok = post_ok
        self.update_ok = update_ok
        self.posts: list[dict[str, str | None]] = []
        self.updates: list[dict[str, str]] = []

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> str | None:
        self.posts.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return f"ts-{len(self.posts)}" if self.post_ok else None

    def update_message(self, *, channel: str, ts: str, text: str) -> bool:
        self.updates.append({"channel": channel, "ts": ts, "text": text})
        return self.update_ok


def _sink(client: _FakeMessagingClient) -> SlackOutputSink:
    return SlackOutputSink(
        client=client,
        channel_id="C222",
        thread_ts="1700.100",
        update_interval_seconds=0.0,
    )


def test_posts_status_placeholder_into_thread_on_creation() -> None:
    client = _FakeMessagingClient()
    _sink(client)

    assert len(client.posts) == 1
    assert client.posts[0]["thread_ts"] == "1700.100"
    assert client.posts[0]["text"]


def test_finalize_replaces_placeholder_with_answer() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("the root cause is a full disk")

    assert client.updates[-1]["ts"] == "ts-1"
    assert client.updates[-1]["text"] == "the root cause is a full disk"
    assert len(client.posts) == 1


def test_finalize_posts_new_message_when_update_fails() -> None:
    client = _FakeMessagingClient(update_ok=False)
    sink = _sink(client)

    sink.finalize("answer")

    assert client.posts[-1]["text"] == "answer"
    assert client.posts[-1]["thread_ts"] == "1700.100"


def test_finalize_truncates_oversized_text() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.finalize("x" * (SLACK_MAX_MESSAGE_CHARS + 1000))

    assert len(client.updates[-1]["text"]) <= SLACK_MAX_MESSAGE_CHARS


def test_stream_returns_full_text_and_updates_preview() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    text = sink.stream(label="assistant", chunks=["hello", " world"])

    assert text == "hello world"
    assert client.updates[-1]["text"] == "hello world"


def test_tool_status_edits_placeholder() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.set_tool_status("Running kubectl get pods")

    assert client.updates
    assert "kubectl" in client.updates[-1]["text"]


def test_render_error_finalizes_with_error_text() -> None:
    client = _FakeMessagingClient()
    sink = _sink(client)

    sink.render_error("provider unavailable")

    assert client.updates[-1]["text"] == "Error: provider unavailable"


def test_survives_failed_placeholder_post() -> None:
    client = _FakeMessagingClient(post_ok=False)
    sink = _sink(client)
    client.post_ok = True

    sink.set_tool_status("working")
    sink.finalize("answer")

    # No placeholder to edit: statuses are dropped, the answer is posted fresh.
    assert not client.updates
    assert client.posts[-1]["text"] == "answer"
