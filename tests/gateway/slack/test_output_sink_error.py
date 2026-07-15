"""Slack output sink must not post raw exception detail to the thread."""

from __future__ import annotations

from gateway.slack.output_sink import SlackOutputSink


class _FakeSlackClient:
    """Captures the text the sink delivers to Slack."""

    def __init__(self) -> None:
        self.finalized = ""

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> str | None:
        return "1.0"

    def update_message(self, *, channel: str, ts: str, text: str) -> bool:
        self.finalized = text
        return True


def test_render_error_delivers_generic_copy_not_exception_detail() -> None:
    # Arrange
    client = _FakeSlackClient()
    sink = SlackOutputSink(client=client, channel_id="C1", thread_ts="1.0")

    # Act: hand render_error a raw exception string with a secret in it.
    sink.render_error("RuntimeError: token sk-DO-NOT-LEAK rejected by upstream")

    # Assert: none of the detail reaches the Slack thread.
    assert "sk-DO-NOT-LEAK" not in client.finalized
    assert "RuntimeError" not in client.finalized
    assert "Something went wrong" in client.finalized
